#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动更新七牛云 CDN SSL 证书脚本
功能：
1. 通过 acme.sh 使用 DNS 方式获取 SSL 证书
2. 使用阿里云 API 自动修改 DNS TXT 记录完成验证
3. 上传证书到七牛云并更新 CDN SSL 配置
4. 记录证书到期时间，智能判断是否需要更新
"""

import os
import sys
import json
import subprocess
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional
import time

try:
    from aliyunsdkcore.client import AcsClient
    from aliyunsdkalidns.request.v20150109 import (
        AddDomainRecordRequest,
        DeleteDomainRecordRequest,
        DescribeDomainRecordsRequest,
        UpdateDomainRecordRequest
    )
    from qiniu import Auth, BucketManager
    import requests
except ImportError as e:
    print(f"缺少必要的依赖包: {e}")
    print("请运行: pip install -r requirements.txt")
    sys.exit(1)


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('auto_ssl.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class SSLCertificateManager:
    """SSL 证书管理器"""

    def __init__(self, config_file='config.json'):
        """初始化配置"""
        self.config = self._load_config(config_file)
        self.cert_record_file = 'cert_records.json'
        self.cert_records = self._load_cert_records()

        # 初始化阿里云客户端
        self.ali_client = AcsClient(
            self.config['aliyun']['access_key_id'],
            self.config['aliyun']['access_key_secret'],
            self.config['aliyun']['region']
        )

        # 初始化七牛云认证
        self.qiniu_auth = Auth(
            self.config['qiniu']['access_key'],
            self.config['qiniu']['secret_key']
        )

    def _load_config(self, config_file: str) -> Dict:
        """加载配置文件"""
        if not os.path.exists(config_file):
            logger.error(f"配置文件 {config_file} 不存在")
            sys.exit(1)

        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _load_cert_records(self) -> Dict:
        """加载证书记录"""
        if os.path.exists(self.cert_record_file):
            try:
                with open(self.cert_record_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"读取证书记录失败: {e}")
                return {}
        return {}

    def _save_cert_records(self):
        """保存证书记录"""
        with open(self.cert_record_file, 'w', encoding='utf-8') as f:
            json.dump(self.cert_records, f, ensure_ascii=False, indent=2)
        logger.info(f"证书记录已保存到 {self.cert_record_file}")

    def check_cert_expiry(self, domain: str) -> bool:
        """
        检查证书是否需要更新
        返回 True 表示需要更新，False 表示不需要
        """
        if domain not in self.cert_records:
            logger.info(f"域名 {domain} 没有证书记录，需要申请新证书")
            return True

        expiry_date_str = self.cert_records[domain].get('expiry_date')
        if not expiry_date_str:
            logger.info(f"域名 {domain} 证书到期时间记录异常，需要更新")
            return True

        try:
            expiry_date = datetime.fromisoformat(expiry_date_str)
            days_remaining = (expiry_date - datetime.now()).days
            threshold_days = self.config.get('renewal_days_before_expiry', 30)

            logger.info(f"域名 {domain} 证书还有 {days_remaining} 天到期")

            if days_remaining <= threshold_days:
                logger.info(f"证书将在 {threshold_days} 天内到期，需要更新")
                return True
            else:
                logger.info(f"证书还有 {days_remaining} 天到期，暂不需要更新")
                return False
        except Exception as e:
            logger.error(f"解析证书到期时间失败: {e}")
            return True

    def get_root_domain(self, domain: str) -> str:
        """获取根域名"""
        parts = domain.split('.')
        if len(parts) >= 2:
            return '.'.join(parts[-2:])
        return domain

    def add_dns_txt_record(self, domain: str, record_value: str) -> Optional[str]:
        """添加 DNS TXT 记录用于 ACME 验证"""
        root_domain = self.get_root_domain(domain)
        record_name = f"_acme-challenge.{domain}".replace(f".{root_domain}", "")

        if record_name.endswith(root_domain):
            record_name = record_name[:-len(root_domain)-1]

        logger.info(f"添加 DNS TXT 记录: {record_name}.{root_domain} = {record_value}")

        try:
            # 先检查是否已存在记录
            existing_record_id = self._find_dns_record(root_domain, record_name, 'TXT')

            if existing_record_id:
                # 更新现有记录
                logger.info(f"更新已存在的 TXT 记录 (ID: {existing_record_id})")
                request = UpdateDomainRecordRequest.UpdateDomainRecordRequest()
                request.set_RecordId(existing_record_id)
                request.set_RR(record_name)
                request.set_Type('TXT')
                request.set_Value(record_value)
                self.ali_client.do_action_with_exception(request)
                return existing_record_id
            else:
                # 添加新记录
                request = AddDomainRecordRequest.AddDomainRecordRequest()
                request.set_DomainName(root_domain)
                request.set_RR(record_name)
                request.set_Type('TXT')
                request.set_Value(record_value)

                response = self.ali_client.do_action_with_exception(request)
                result = json.loads(response)
                record_id = result['RecordId']
                logger.info(f"DNS TXT 记录添加成功，记录 ID: {record_id}")

                # 等待 DNS 记录生效
                logger.info("等待 DNS 记录生效 (60秒)...")
                time.sleep(60)

                return record_id
        except Exception as e:
            logger.error(f"添加 DNS TXT 记录失败: {e}")
            return None

    def _find_dns_record(self, domain: str, rr: str, record_type: str) -> Optional[str]:
        """查找 DNS 记录"""
        try:
            request = DescribeDomainRecordsRequest.DescribeDomainRecordsRequest()
            request.set_DomainName(domain)
            request.set_RRKeyWord(rr)
            request.set_TypeKeyWord(record_type)

            response = self.ali_client.do_action_with_exception(request)
            result = json.loads(response)

            records = result.get('DomainRecords', {}).get('Record', [])
            if records:
                return records[0]['RecordId']
            return None
        except Exception as e:
            logger.error(f"查询 DNS 记录失败: {e}")
            return None

    def delete_dns_txt_record(self, record_id: str):
        """删除 DNS TXT 记录"""
        if not record_id:
            return

        try:
            request = DeleteDomainRecordRequest.DeleteDomainRecordRequest()
            request.set_RecordId(record_id)
            self.ali_client.do_action_with_exception(request)
            logger.info(f"DNS TXT 记录已删除 (ID: {record_id})")
        except Exception as e:
            logger.error(f"删除 DNS TXT 记录失败: {e}")

    def check_certificate_exists(self, domain: str) -> bool:
        """检查证书文件是否已经存在"""
        cert_paths = self.get_certificate_paths(domain)

        # 检查必要的证书文件是否都存在
        if os.path.exists(cert_paths['cert']) and os.path.exists(cert_paths['key']):
            logger.info(f"域名 {domain} 的证书文件已存在")
            return True

        logger.info(f"域名 {domain} 的证书文件不存在")
        return False

    def issue_certificate(self, domain: str, force_renew: bool = False) -> bool:
        """使用 acme.sh 申请证书

        Args:
            domain: 域名
            force_renew: 是否强制续期（用于证书到期需要更新的情况）
        """
        logger.info(f"开始为域名 {domain} 申请证书...")

        acme_home = os.path.expanduser(self.config.get('acme_home', '~/.acme.sh'))
        email = self.config.get('email', '')
        acme_server = self.config.get('acme_server', 'letsencrypt')

        # 检查证书是否已存在
        cert_exists = self.check_certificate_exists(domain)

        if cert_exists and force_renew:
            # 证书已存在且需要强制续期，使用 --renew --force
            logger.info(f"证书已存在，使用强制续期模式")
            cmd = [
                f"{acme_home}/acme.sh",
                "--renew",
                "--force",
                "-d", domain,
                "--yes-I-know-dns-manual-mode-enough-go-ahead-please"
            ]
        else:
            # 首次申请证书，使用 --issue
            cmd = [
                f"{acme_home}/acme.sh",
                "--issue",
                "--dns",
                "-d", domain,
                "--yes-I-know-dns-manual-mode-enough-go-ahead-please"
            ]

        if email:
            cmd.extend(["--accountemail", email])

        # 添加 ACME 服务器配置
        if acme_server and acme_server.lower() != 'letsencrypt':
            cmd.extend(["--server", acme_server])
            logger.info(f"使用 ACME 服务器: {acme_server}")
        else:
            logger.info(f"使用默认 ACME 服务器: Let's Encrypt")

        try:
            # 第一次运行获取需要设置的 TXT 记录值
            logger.info("运行 acme.sh 获取 TXT 记录值...")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8'
            )

            # 从输出中提取 TXT 记录值
            txt_value = self._extract_txt_value(result.stdout + result.stderr)

            if not txt_value:
                logger.error("无法从 acme.sh 输出中提取 TXT 记录值")
                logger.error(f"acme.sh 输出: {result.stdout}")
                logger.error(f"acme.sh 错误: {result.stderr}")
                return False

            logger.info(f"提取到 TXT 记录值: {txt_value}")

            # 添加 DNS TXT 记录
            record_id = self.add_dns_txt_record(domain, txt_value)

            if not record_id:
                logger.error("添加 DNS TXT 记录失败")
                return False

            # 再次运行 acme.sh 完成验证和证书签发
            logger.info("再次运行 acme.sh 完成证书签发...")
            cmd_renew = [
                f"{acme_home}/acme.sh",
                "--renew",
                "-d", domain,
                "--yes-I-know-dns-manual-mode-enough-go-ahead-please"
            ]

            result_renew = subprocess.run(
                cmd_renew,
                capture_output=True,
                text=True,
                encoding='utf-8'
            )

            # 清理 DNS TXT 记录
            self.delete_dns_txt_record(record_id)

            if result_renew.returncode == 0:
                logger.info("证书申请成功！")
                return True
            else:
                logger.error(f"证书申请失败: {result_renew.stderr}")
                return False

        except Exception as e:
            logger.error(f"申请证书时发生异常: {e}")
            return False

    def _extract_txt_value(self, output: str) -> Optional[str]:
        """从 acme.sh 输出中提取 TXT 记录值"""
        lines = output.split('\n')
        for line in lines:
            if 'TXT value' in line or 'txt value' in line.lower():
                # 尝试提取引号中的值
                parts = line.split("'")
                if len(parts) >= 2:
                    return parts[1]
                parts = line.split('"')
                if len(parts) >= 2:
                    return parts[1]

        # 另一种提取方式：查找特定格式
        for line in lines:
            if '_acme-challenge' in line and '=' in line:
                parts = line.split('=')
                if len(parts) >= 2:
                    value = parts[1].strip().strip("'\"")
                    if value:
                        return value

        return None

    def get_certificate_paths(self, domain: str) -> Dict[str, str]:
        """获取证书文件路径"""
        acme_home = os.path.expanduser(self.config.get('acme_home', '~/.acme.sh'))

        # acme.sh 默认使用 ECC 证书，目录名为 domain_ecc
        # 先尝试 ECC 目录，如果不存在则使用普通目录
        cert_dir_ecc = os.path.join(acme_home, f'{domain}_ecc')
        cert_dir_rsa = os.path.join(acme_home, domain)

        if os.path.exists(cert_dir_ecc):
            cert_dir = cert_dir_ecc
            logger.info(f"使用 ECC 证书目录: {cert_dir}")
        else:
            cert_dir = cert_dir_rsa
            logger.info(f"使用 RSA 证书目录: {cert_dir}")

        return {
            'cert': os.path.join(cert_dir, 'fullchain.cer'),
            'key': os.path.join(cert_dir, f'{domain}.key'),
            'ca': os.path.join(cert_dir, 'ca.cer')
        }

    def upload_cert_to_qiniu(self, domain: str) -> bool:
        """上传证书到七牛云"""
        logger.info(f"开始上传证书到七牛云...")

        cert_paths = self.get_certificate_paths(domain)

        # 读取证书文件
        try:
            with open(cert_paths['cert'], 'r') as f:
                cert_content = f.read()
            with open(cert_paths['key'], 'r') as f:
                key_content = f.read()

            logger.info("证书文件读取成功")
        except Exception as e:
            logger.error(f"读取证书文件失败: {e}")
            return False

        # 上传证书到七牛云
        try:
            cert_name = f"{domain}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

            # 使用七牛云 SSL 证书上传 API（注意：使用 HTTP 而不是 HTTPS）
            url = 'http://api.qiniu.com/sslcert'

            data = {
                'name': cert_name,
                'common_name': domain,
                'pri': key_content,
                'ca': cert_content
            }

            # 生成七牛云认证 token
            # 使用 token_of_request() 方法生成管理凭证，并添加 QBox 前缀
            token = f"QBox {self.qiniu_auth.token_of_request(url)}"

            headers = {
                'Content-Type': 'application/json',
                'Authorization': token
            }

            response = requests.post(url, headers=headers, json=data)

            if response.status_code == 200:
                result = response.json()
                cert_id = result.get('certID')
                logger.info(f"证书上传成功，证书 ID: {cert_id}")

                # 更新 CDN 域名的 SSL 配置
                return self.update_cdn_ssl_config(domain, cert_id)
            else:
                logger.error(f"上传证书失败: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"上传证书到七牛云时发生异常: {e}")
            return False

    def update_cdn_ssl_config(self, domain: str, cert_id: str) -> bool:
        """更新七牛云 CDN 的 SSL 配置"""
        logger.info(f"更新 CDN 域名 {domain} 的 SSL 配置...")

        try:
            # 七牛云 CDN HTTPS 配置 API
            url = f'http://api.qiniu.com/domain/{domain}/httpsconf'

            data = {
                'certid': cert_id,
                'forceHttps': self.config['qiniu'].get('force_https', False)
            }

            # 生成七牛云认证 token
            # 使用 token_of_request() 方法生成管理凭证，并添加 QBox 前缀
            token = f"QBox {self.qiniu_auth.token_of_request(url)}"

            headers = {
                'Content-Type': 'application/json',
                'Authorization': token
            }

            response = requests.put(url, headers=headers, json=data)

            if response.status_code == 200:
                logger.info(f"CDN SSL 配置更新成功")
                return True
            else:
                logger.error(f"更新 CDN SSL 配置失败: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"更新 CDN SSL 配置时发生异常: {e}")
            return False

    def update_cert_record(self, domain: str):
        """更新证书记录"""
        # 证书有效期通常是 90 天
        expiry_date = datetime.now() + timedelta(days=90)

        self.cert_records[domain] = {
            'expiry_date': expiry_date.isoformat(),
            'updated_at': datetime.now().isoformat(),
            'status': 'active'
        }

        self._save_cert_records()

    def process_domain(self, domain: str) -> bool:
        """处理单个域名的证书更新"""
        logger.info(f"\n{'='*60}")
        logger.info(f"开始处理域名: {domain}")
        logger.info(f"{'='*60}\n")

        # 检查证书是否需要更新
        need_renewal = self.check_cert_expiry(domain)

        if not need_renewal:
            logger.info(f"域名 {domain} 证书无需更新")
            return True

        # 检查证书文件是否已经存在
        cert_exists = self.check_certificate_exists(domain)

        if cert_exists:
            # 证书文件已存在
            # 情况1: 证书到期需要续期 - 使用强制续期模式
            # 情况2: 之前申请成功但上传失败 - 跳过申请直接上传
            # 通过检查 cert_records.json 来判断是哪种情况
            if domain in self.cert_records and self.cert_records[domain].get('status') == 'active':
                # 证书记录存在且状态为 active，说明是证书到期需要续期
                logger.info(f"域名 {domain} 证书即将到期，执行强制续期")
                if not self.issue_certificate(domain, force_renew=True):
                    logger.error(f"域名 {domain} 证书续期失败")
                    return False
            else:
                # 证书记录不存在或状态异常，可能是之前申请成功但上传失败
                logger.info(f"域名 {domain} 证书文件已存在，跳过申请步骤，直接上传")
        else:
            # 证书文件不存在，首次申请
            logger.info(f"域名 {domain} 首次申请证书")
            if not self.issue_certificate(domain, force_renew=False):
                logger.error(f"域名 {domain} 证书申请失败")
                return False

        # 上传证书到七牛云
        if not self.upload_cert_to_qiniu(domain):
            logger.error(f"域名 {domain} 证书上传失败")
            return False

        # 更新证书记录
        self.update_cert_record(domain)

        logger.info(f"域名 {domain} 证书更新完成！\n")
        return True

    def run(self):
        """运行主流程"""
        logger.info("="*60)
        logger.info("自动更新 SSL 证书任务开始")
        logger.info(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("="*60)

        domains = self.config.get('domains', [])

        if not domains:
            logger.warning("配置文件中没有需要处理的域名")
            return

        success_count = 0
        fail_count = 0

        for domain in domains:
            try:
                if self.process_domain(domain):
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"处理域名 {domain} 时发生异常: {e}")
                fail_count += 1

        logger.info("\n" + "="*60)
        logger.info("任务执行完成")
        logger.info(f"成功: {success_count} 个域名")
        logger.info(f"失败: {fail_count} 个域名")
        logger.info("="*60)


def main():
    """主入口函数"""
    try:
        manager = SSLCertificateManager()
        manager.run()
    except KeyboardInterrupt:
        logger.info("\n任务被用户中断")
        sys.exit(0)
    except Exception as e:
        logger.error(f"程序执行出错: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
