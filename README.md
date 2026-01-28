# 七牛云 CDN SSL 证书自动更新脚本

自动化管理七牛云 CDN 域名的 SSL 证书，支持通过 acme.sh + 阿里云 DNS 验证方式自动申请和更新 Let's Encrypt 证书。

## 功能特性

- ✅ 使用 acme.sh 通过 DNS 方式自动申请 SSL 证书
- ✅ 支持多个 ACME 证书服务商（Let's Encrypt、ZeroSSL、BuyPass 等）
- ✅ 自动通过阿里云 API 添加和删除 DNS TXT 验证记录
- ✅ 自动上传证书到七牛云 CDN 并更新 SSL 配置
- ✅ 智能检测证书到期时间，自动续期（默认到期前 30 天）
- ✅ 支持多域名批量管理
- ✅ 完整的日志记录和错误处理
- ✅ 适合通过 crontab 定时执行

## 环境要求

- Python 3.6+
- acme.sh (需提前安装)
- 阿里云账号（用于 DNS 管理）
- 七牛云账号（用于 CDN 管理）

## 安装步骤

### 1. 安装 acme.sh

```bash
curl https://get.acme.sh | sh -s email=your-email@example.com
source ~/.bashrc
```

### 2. 克隆或下载本项目

```bash
cd /path/to/your/project
```

### 3. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 4. 配置文件

复制配置文件模板并修改：

```bash
cp config.json.example config.json
```

编辑 `config.json`，填入你的实际配置信息：

```json
{
  "domains": [
    "example.com",
    "cdn.example.com"
  ],
  "email": "your-email@example.com",
  "aliyun": {
    "access_key_id": "YOUR_ALIYUN_ACCESS_KEY_ID",
    "access_key_secret": "YOUR_ALIYUN_ACCESS_KEY_SECRET",
    "region": "cn-hangzhou"
  },
  "qiniu": {
    "access_key": "YOUR_QINIU_ACCESS_KEY",
    "secret_key": "YOUR_QINIU_SECRET_KEY",
    "force_https": false
  },
  "acme_home": "~/.acme.sh",
  "acme_server": "letsencrypt",
  "renewal_days_before_expiry": 7
}
```

#### 配置说明

| 配置项 | 说明 |
|--------|------|
| `domains` | 需要申请和更新证书的域名列表 |
| `email` | 用于 ACME 账户注册的邮箱地址 |
| `aliyun.access_key_id` | 阿里云 AccessKey ID |
| `aliyun.access_key_secret` | 阿里云 AccessKey Secret |
| `aliyun.region` | 阿里云地域（如 cn-hangzhou、cn-beijing） |
| `qiniu.access_key` | 七牛云 AccessKey |
| `qiniu.secret_key` | 七牛云 SecretKey |
| `qiniu.force_https` | 是否强制 HTTPS 跳转 |
| `acme_home` | acme.sh 安装目录，默认 ~/.acme.sh |
| `acme_server` | ACME 证书服务商，默认 letsencrypt，可选 zerossl、buypass、google 等 |
| `renewal_days_before_expiry` | 到期前多少天开始续期，建议 30 天 |

#### ACME 证书服务商

`acme_server` 配置项支持以下证书服务商：

| 服务商 | 配置值 | 说明 |
|--------|--------|------|
| **Let's Encrypt** | `letsencrypt` 或留空 | 默认服务商，免费，广泛信任 |
| **ZeroSSL** | `zerossl` | 免费，支持 90 天证书 |
| **BuyPass** | `buypass` | 免费，挪威证书机构 |
| **Google Trust Services** | `google` | 需要额外的 EAB 凭据配置 |

**推荐使用 Let's Encrypt**，这是最常用和最稳定的选择。如果需要更换服务商，只需修改 `acme_server` 配置项即可。

### 5. 获取阿里云 AccessKey

1. 登录 [阿里云控制台](https://ram.console.aliyun.com/manage/ak)
2. 创建 AccessKey，并确保该账号有 DNS 管理权限
3. 记录 AccessKey ID 和 AccessKey Secret

### 6. 获取七牛云 AccessKey

1. 登录 [七牛云控制台](https://portal.qiniu.com/user/key)
2. 在"密钥管理"中查看 AccessKey 和 SecretKey
3. 确保账号有 CDN 和证书管理权限

## 使用方法

### 手动执行

```bash
python auto_ssl.py
```

### 设置定时任务

使用 crontab 设置每天凌晨 1 点自动执行：

```bash
crontab -e
```

添加以下行：

```cron
0 1 * * * cd /path/to/auto-ssl && /usr/bin/python3 auto_ssl.py >> /path/to/auto-ssl/cron.log 2>&1
```

**注意**：
- 替换 `/path/to/auto-ssl` 为实际脚本路径
- 替换 `/usr/bin/python3` 为实际 Python 路径（使用 `which python3` 查看）

## 工作流程

```
1. 读取配置文件和证书记录
   ↓
2. 检查每个域名的证书是否需要更新
   ↓
3. 如果需要更新：
   a. 使用 acme.sh 发起证书申请
   b. 提取 DNS TXT 验证记录值
   c. 通过阿里云 API 添加 TXT 记录
   d. 等待 DNS 生效（60秒）
   e. 完成 ACME 验证并获取证书
   f. 清理 DNS TXT 记录
   g. 上传证书到七牛云
   h. 更新 CDN 域名的 SSL 配置
   i. 记录证书到期时间
   ↓
4. 输出执行结果和日志
```

## 文件说明

```
auto-ssl/
├── auto_ssl.py              # 主脚本
├── config.json              # 配置文件（需自行创建）
├── config.json.example      # 配置文件模板
├── requirements.txt         # Python 依赖
├── README.md               # 使用说明
├── cert_records.json       # 证书记录文件（自动生成）
├── auto_ssl.log           # 运行日志（自动生成）
└── cron.log               # crontab 日志（可选）
```

## 证书记录文件

脚本会自动在同级目录创建 `cert_records.json` 文件，记录每个域名的证书信息：

```json
{
  "example.com": {
    "expiry_date": "2026-04-27T10:30:00",
    "updated_at": "2026-01-27T10:30:00",
    "status": "active"
  }
}
```

## 日志

- **auto_ssl.log**: 详细的运行日志，包含所有操作记录和错误信息
- **cron.log**: crontab 执行日志（如果设置了输出重定向）

## 常见问题

### 1. acme.sh 命令找不到

确保 acme.sh 已正确安装，并检查 `config.json` 中的 `acme_home` 路径是否正确。

```bash
ls ~/.acme.sh/acme.sh
```

### 2. DNS 验证失败

- 检查阿里云 AccessKey 是否有 DNS 管理权限
- 确认域名的 DNS 服务器是阿里云
- 查看日志确认 TXT 记录是否成功添加

### 3. 七牛云上传失败

- 检查七牛云 AccessKey 和 SecretKey 是否正确
- 确认账号有 CDN 和证书管理权限
- 查看 `auto_ssl.log` 获取详细错误信息

### 4. 证书路径问题

默认证书路径为 `~/.acme.sh/域名/`，如果修改过 acme.sh 配置，需要相应修改脚本中的路径。

## 安全建议

1. **保护配置文件**: config.json 包含敏感信息，建议设置文件权限：
   ```bash
   chmod 600 config.json
   ```

2. **使用子账号**: 建议为阿里云和七牛云创建子账号，只授予必要的权限

3. **定期检查日志**: 定期查看 `auto_ssl.log` 确保脚本正常运行

## 更新日志

### v1.1.0 (2026-01-28)

- 新增 `acme_server` 配置项，支持选择不同的 ACME 证书服务商
- 支持 Let's Encrypt、ZeroSSL、BuyPass、Google Trust Services 等服务商
- 默认使用 Let's Encrypt

### v1.0.0 (2026-01-27)

- 初始版本发布
- 支持 acme.sh + 阿里云 DNS 验证
- 支持七牛云 CDN 证书自动更新
- 支持多域名批量管理
- 智能证书到期检测

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！

## 联系方式

如有问题，请提交 Issue 或通过邮件联系。

---

**注意**: 本脚本仅供学习和个人使用，请遵守相关服务的使用条款。
