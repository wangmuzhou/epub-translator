import hashlib
import hmac
import io
import datetime
import json
import os
import re
import zipfile
from xml.etree import ElementTree as ET
from flask import Flask, request, Response, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

ACCESS_KEY = os.environ.get("VOLC_ACCESS_KEY", "")
SECRET_KEY = os.environ.get("VOLC_SECRET_KEY", "")
SERVICE    = "translate"
VERSION    = "2020-06-01"
REGION     = "cn-north-1"
HOST       = "open.volcengineapi.com"
ACTION     = "TranslateText"

TRANSLATABLE_TAGS = {"p","h1","h2","h3","h4","h5","h6","li","td","th","caption","blockquote","title"}
SKIP_TAGS         = {"script","style","code","pre","svg","math"}


@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "ok"})


@app.route("/api/translate", methods=["POST"])
def translate():
    print(f"收到请求, ACCESS_KEY长度: {len(ACCESS_KEY)}, SECRET_KEY长度: {len(SECRET_KEY)}", flush=True)
    if not ACCESS_KEY or not SECRET_KEY:
        return jsonify({"error": "服务器未配置翻译 API Key"}), 500
    if "file" not in request.files:
        return jsonify({"error": "缺少 epub 文件"}), 400
    file        = request.files["file"]
    source_lang = request.form.get("source_lang", "en")
    target_lang = request.form.get("target_lang", "zh")
    epub_bytes  = file.read()
    print(f"文件大小: {len(epub_bytes)} bytes, 源语言: {source_lang}, 目标语言: {target_lang}", flush=True)
    if len(epub_bytes) > 50 * 1024 * 1024:
        return jsonify({"error": "文件超过 50MB 限制"}), 400
    try:
        result = translate_epub_bytes(epub_bytes, source_lang, target_lang)
        print(f"翻译完成, 输出大小: {len(result)} bytes", flush=True)
        return Response(
            result,
            mimetype="application/epub+zip",
            headers={"Content-Disposition": 'attachment; filename="translated.epub"'}
        )
    except Exception as e:
        print(f"翻译失败: {e}", flush=True)
        return jsonify({"error": str(e)}), 500


def translate_epub_bytes(epub_bytes, source_lang, target_lang):
    raw_files = {}
    with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as zf:
        for name in zf.namelist():
            raw_files[name] = zf.read(name)
    opf_path   = get_opf_path(raw_files.get("META-INF/container.xml", b""))
    xhtml_list = get_spine(opf_path, raw_files.get(opf_path, b""))
    print(f"找到 {len(xhtml_list)} 个内容文件", flush=True)
    print(f"OPF路径: {opf_path}, 所有文件: {list(raw_files.keys())[:15]}", flush=True)
    for path in xhtml_list:
        raw = raw_files.get(path)
        if raw:
            try:
                raw_files[path] = translate_xhtml(raw, source_lang, target_lang)
            except Exception as e:
                print(f"文件 {path} 翻译失败: {e}", flush=True)
    return pack_epub(raw_files)


def get_opf_path(container_xml):
    try:
        for el in ET.fromstring(container_xml).iter():
            if el.tag.endswith("rootfile"):
                return el.attrib.get("full-path", "")
    except Exception:
        pass
    return "OEBPS/content.opf"


def get_spine(opf_path, opf_bytes):
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


def translate_xhtml(raw, source_lang, target_lang):
    text = raw.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return raw
    nodes = collect_nodes(root)
    print(f"找到 {len(nodes)} 个文本节点", flush=True)
    for el, inner in nodes:
        if not inner.strip():
            continue
        try:
            translated = volc_translate(inner, source_lang, target_lang)
            set_inner(el, translated)
        except Exception as e:
            print(f"节点翻译失败: {e} | 原文: {inner[:50]}", flush=True)
            raise
    result = ET.tostring(root, encoding="unicode")
    if text.startswith("<?xml"):
        result = '<?xml version="1.0" encoding="UTF-8"?>\n' + result
    return result.encode("utf-8")


def collect_nodes(root):
    results = []
    def walk(el):
        tag = el.tag.split("}")[-1].lower() if "}" in el.tag else el.tag.lower()
        if tag in SKIP_TAGS:
            return
        if tag in TRANSLATABLE_TAGS:
            inner = get_inner(el).strip()
            if inner and not re.fullmatch(r"[\d\s\W]+", inner):
                results.append((el, inner))
            return
        for child in el:
            walk(child)
    walk(root)
    return results


def get_inner(el):
    buf = io.StringIO()
    if el.text:
        buf.write(el.text)
    for child in el:
        buf.write(ET.tostring(child, encoding="unicode"))
    return buf.getvalue()


def set_inner(el, html_str):
    try:
        tmp = ET.fromstring(f"<_w_>{html_str}</_w_>")
        el.text = tmp.text
        el[:] = list(tmp)
    except ET.ParseError:
        el.text = html_str
        el[:] = []


def pack_epub(raw_files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if "mimetype" in raw_files:
            zf.writestr(zipfile.ZipInfo("mimetype"), raw_files["mimetype"], zipfile.ZIP_STORED)
        for name, data in raw_files.items():
            if name != "mimetype":
                zf.writestr(name, data)
    return buf.getvalue()


def sign(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def volc_translate(text, source_lang, target_lang):
    import urllib.request
    body_str = json.dumps({"SourceLanguage": source_lang, "TargetLanguage": target_lang, "TextList": [text]})
    now      = datetime.datetime.utcnow()
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%Y%m%dT%H%M%SZ")
    ph       = hashlib.sha256(body_str.encode()).hexdigest()
    ch       = f"content-type:application/json\nhost:{HOST}\nx-date:{time_str}\n"
    sh       = "content-type;host;x-date"
    cr       = "\n".join(["POST", "/", f"Action={ACTION}&Version={VERSION}", ch, sh, ph])
    cs       = f"{date_str}/{REGION}/{SERVICE}/request"
    s2s      = "\n".join(["HMAC-SHA256", time_str, cs, hashlib.sha256(cr.encode()).hexdigest()])
    sk2      = sign(sign(sign(sign(("VOLC" + SECRET_KEY).encode(), date_str), REGION), SERVICE), "request")
    sig      = hmac.new(sk2, s2s.encode(), hashlib.sha256).hexdigest()
    auth     = f"HMAC-SHA256 Credential={ACCESS_KEY}/{cs}, SignedHeaders={sh}, Signature={sig}"
    url      = f"https://{HOST}?Action={ACTION}&Version={VERSION}"
    req      = urllib.request.Request(
        url, data=body_str.encode(),
        headers={"Content-Type": "application/json", "Host": HOST, "X-Date": time_str, "Authorization": auth},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    print(f"火山引擎返回: {data}", flush=True)
    return data["TranslationList"][0]["Translation"]


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
