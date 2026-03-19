"""
api/translate.py  —  Vercel Serverless Function
接收 epub 文件 → 火山引擎翻译 → 返回翻译后 epub
"""
import base64
import hashlib
import hmac
import io
import datetime
import json
import os
import re
import zipfile
from http.server import BaseHTTPRequestHandler
from xml.etree import ElementTree as ET
import urllib.request
import urllib.parse

# ── 火山引擎配置（从环境变量读取）────────────────
ACCESS_KEY = os.environ.get("VOLC_ACCESS_KEY", "")
SECRET_KEY = os.environ.get("VOLC_SECRET_KEY", "")
SERVICE    = "translate"
VERSION    = "2020-06-01"
REGION     = "cn-north-1"
HOST       = "open.volcengineapi.com"
ACTION     = "TranslateText"

TRANSLATABLE_TAGS = {"p","h1","h2","h3","h4","h5","h6","li","td","th","caption","blockquote","title"}
SKIP_TAGS         = {"script","style","code","pre","svg","math"}

MAX_FILE_MB = 50  # 最大文件限制


# ══════════════════════════════════════════════
# Vercel Handler
# ══════════════════════════════════════════════
class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        if self.path != "/api/translate":
            self._json(404, {"error": "not found"})
            return
        try:
            content_type = self.headers.get("Content-Type", "")
            content_len  = int(self.headers.get("Content-Length", 0))
            body         = self.rfile.read(content_len)

            # 解析 multipart/form-data
            boundary = _get_boundary(content_type)
            if not boundary:
                self._json(400, {"error": "需要 multipart/form-data"})
                return

            parts = _parse_multipart(body, boundary.encode())
            epub_bytes  = parts.get("file")
            source_lang = parts.get("source_lang", b"en").decode()
            target_lang = parts.get("target_lang", b"zh").decode()

            if not epub_bytes:
                self._json(400, {"error": "缺少 epub 文件"})
                return
            if len(epub_bytes) > MAX_FILE_MB * 1024 * 1024:
                self._json(400, {"error": f"文件超过 {MAX_FILE_MB}MB 限制"})
                return
            if not ACCESS_KEY or not SECRET_KEY:
                self._json(500, {"error": "服务器未配置翻译 API Key"})
                return

            # 翻译
            result = translate_epub_bytes(epub_bytes, source_lang, target_lang)

            self.send_response(200)
            self.send_header("Content-Type", "application/epub+zip")
            self.send_header("Content-Disposition", 'attachment; filename="translated.epub"')
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

        except Exception as e:
            self._json(500, {"error": str(e)})

    def do_GET(self):
        self._json(200, {"status": "ok", "message": "EPUB 翻译 API 正常运行"})

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args): pass


# ══════════════════════════════════════════════
# Multipart 解析
# ══════════════════════════════════════════════
def _get_boundary(content_type: str) -> str:
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            return part[9:].strip('"')
    return ""

def _parse_multipart(body: bytes, boundary: bytes) -> dict:
    parts = {}
    delimiter = b"--" + boundary
    segments  = body.split(delimiter)
    for seg in segments[1:]:
        if seg.strip() in (b"", b"--", b"--\r\n"):
            continue
        if b"\r\n\r\n" not in seg:
            continue
        headers_raw, content = seg.split(b"\r\n\r\n", 1)
        content = content.rstrip(b"\r\n")
        headers_str = headers_raw.decode("utf-8", errors="replace")
        name = re.search(r'name="([^"]+)"', headers_str)
        if name:
            parts[name.group(1)] = content
    return parts


# ══════════════════════════════════════════════
# EPUB 翻译核心
# ══════════════════════════════════════════════
def translate_epub_bytes(epub_bytes: bytes, source_lang: str, target_lang: str) -> bytes:
    raw_files = {}
    with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as zf:
        for name in zf.namelist():
            raw_files[name] = zf.read(name)

    opf_path   = _get_opf_path(raw_files.get("META-INF/container.xml", b""))
    xhtml_list = _get_spine(opf_path, raw_files.get(opf_path, b""))

    for path in xhtml_list:
        raw = raw_files.get(path)
        if raw:
            try:
                raw_files[path] = _translate_xhtml(raw, source_lang, target_lang)
            except Exception:
                pass

    return _pack_epub(raw_files)


def _get_opf_path(container_xml: bytes) -> str:
    try:
        for el in ET.fromstring(container_xml).iter():
            if el.tag.endswith("rootfile"):
                return el.attrib.get("full-path", "")
    except Exception:
        pass
    return "OEBPS/content.opf"


def _get_spine(opf_path: str, opf_bytes: bytes) -> list:
    import posixpath
    opf_dir = posixpath.dirname(opf_path)
    try:
        root = ET.fromstring(opf_bytes)
    except Exception:
        return []
    manifest = {}
    for el in root.iter():
        if el.tag.endswith("}item") or el.tag == "item":
            mt = el.attrib.get("media-type", "")
            if mt in ("application/xhtml+xml", "text/html"):
                full = posixpath.join(opf_dir, el.attrib.get("href", "")).lstrip("/")
                manifest[el.attrib.get("id", "")] = full
    result = []
    for el in root.iter():
        if el.tag.endswith("}itemref") or el.tag == "itemref":
            idref = el.attrib.get("idref", "")
            if idref in manifest:
                result.append(manifest[idref])
    return result


def _translate_xhtml(raw: bytes, source_lang: str, target_lang: str) -> bytes:
    text = raw.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return raw

    for el, inner in _collect_nodes(root):
        if not inner.strip():
            continue
        try:
            translated = volc_translate(inner, source_lang, target_lang)
            _set_inner(el, translated)
        except Exception:
            pass

    result = ET.tostring(root, encoding="unicode")
    if text.startswith("<?xml"):
        result = '<?xml version="1.0" encoding="UTF-8"?>\n' + result
    return result.encode("utf-8")


def _collect_nodes(root):
    results = []
    def walk(el):
        tag = el.tag.split("}")[-1].lower() if "}" in el.tag else el.tag.lower()
        if tag in SKIP_TAGS:
            return
        if tag in TRANSLATABLE_TAGS:
            inner = _get_inner(el).strip()
            if inner and not re.fullmatch(r"[\d\s\W]+", inner):
                results.append((el, inner))
            return
        for child in el:
            walk(child)
    walk(root)
    return results


def _get_inner(el) -> str:
    buf = io.StringIO()
    if el.text:
        buf.write(el.text)
    for child in el:
        buf.write(ET.tostring(child, encoding="unicode"))
    return buf.getvalue()


def _set_inner(el, html_str: str):
    try:
        tmp = ET.fromstring(f"<_w_>{html_str}</_w_>")
        el.text = tmp.text
        el[:] = list(tmp)
    except ET.ParseError:
        el.text = html_str
        el[:] = []


def _pack_epub(raw_files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if "mimetype" in raw_files:
            zf.writestr(zipfile.ZipInfo("mimetype"), raw_files["mimetype"], zipfile.ZIP_STORED)
        for name, data in raw_files.items():
            if name != "mimetype":
                zf.writestr(name, data)
    return buf.getvalue()


# ══════════════════════════════════════════════
# 火山引擎翻译 API
# ══════════════════════════════════════════════
def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

def volc_translate(text: str, source_lang: str, target_lang: str) -> str:
    body_str = json.dumps({
        "SourceLanguage": source_lang,
        "TargetLanguage": target_lang,
        "TextList": [text],
    })

    now      = datetime.datetime.utcnow()
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%Y%m%dT%H%M%SZ")

    payload_hash     = hashlib.sha256(body_str.encode()).hexdigest()
    canonical_headers = f"content-type:application/json\nhost:{HOST}\nx-date:{time_str}\n"
    signed_headers    = "content-type;host;x-date"
    canonical_request = "\n".join([
        "POST", "/", f"Action={ACTION}&Version={VERSION}",
        canonical_headers, signed_headers, payload_hash,
    ])
    credential_scope = f"{date_str}/{REGION}/{SERVICE}/request"
    string_to_sign   = "\n".join([
        "HMAC-SHA256", time_str, credential_scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    signing_key = _sign(
        _sign(_sign(_sign(("VOLC" + SECRET_KEY).encode(), date_str), REGION), SERVICE),
        "request",
    )
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"HMAC-SHA256 Credential={ACCESS_KEY}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    url = f"https://{HOST}?Action={ACTION}&Version={VERSION}"
    req = urllib.request.Request(
        url,
        data=body_str.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Host": HOST,
            "X-Date": time_str,
            "Authorization": authorization,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    return data["TranslationList"][0]["Translation"]
