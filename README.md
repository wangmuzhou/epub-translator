# EPUB 翻译器 — 部署指南

## 项目结构

```
epub-translator/
├── public/
│   └── index.html      # 前端页面
├── api/
│   └── translate.py    # 后端翻译接口
├── vercel.json         # Vercel 配置
└── requirements.txt    # Python 依赖（空）
```

---

## 部署步骤（10分钟完成）

### 第一步：上传到 GitHub

1. 打开 https://github.com，登录/注册
2. 点右上角 **「+」→「New repository」**
3. 仓库名填 `epub-translator`，选 **Public**，点 **Create**
4. 点 **「uploading an existing file」**
5. 把以下文件按结构上传：
   - `public/index.html`
   - `api/translate.py`
   - `vercel.json`
   - `requirements.txt`
6. 点 **Commit changes**

---

### 第二步：部署到 Vercel

1. 打开 https://vercel.com，用 GitHub 账号登录
2. 点 **「Add New Project」**
3. 选择你的 `epub-translator` 仓库，点 **Import**
4. 直接点 **「Deploy」**（不用改任何设置）
5. 等待 1-2 分钟，部署完成！

---

### 第三步：配置火山引擎 API Key（重要！）

1. 在 Vercel 项目页面，点顶部 **「Settings」**
2. 左侧点 **「Environment Variables」**
3. 添加两个变量：

| Name | Value |
|---|---|
| `VOLC_ACCESS_KEY` | 你的 Access Key ID |
| `VOLC_SECRET_KEY` | 你的 Secret Access Key |

4. 点 **Save**
5. 回到 **「Deployments」** 页面，点最新部署右侧的 **「...」→「Redeploy」**

---

### 第四步：访问你的网站

部署完成后，Vercel 会给你一个地址，格式类似：
```
https://epub-translator-xxx.vercel.app
```

打开这个地址，上传 epub 文件，填入参数，点翻译就可以用了！

---

## 免费额度说明

| 服务 | 免费额度 |
|---|---|
| Vercel | 每月 100GB 流量，无服务器费用 |
| 火山引擎翻译 | 每月 200 万字符 |

对于个人使用完全够用。

---

## 常见问题

**Q: 翻译超时怎么办？**
A: Vercel 免费版函数最长运行 10 秒，大文件可能超时。建议翻译小于 5MB 的 epub 文件。

**Q: 怎么绑定自己的域名？**
A: 在 Vercel「Settings → Domains」添加你的域名，按提示配置 DNS 即可。

**Q: 可以商用吗？**
A: 可以，但注意火山引擎翻译超出免费额度后会收费。
