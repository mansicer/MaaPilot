"""森空岛登录要的设备号 dId：数美（Shumei）设备指纹接口给的 deviceId，前面加 "B"。

2024-09 起森空岛换 cred（generate_cred_by_code）要在请求头带 dId，空的会被当成无效设备。这里照网页版 SDK 的做法
拼一份浏览器环境、按规则加密后发给数美，拿回 deviceId。非官方：接口、密钥、字段都是社区从网页 JS 里扒出来的，
随时可能变。

改写自 arknights-mower（arknights_mower/utils/SecuritySm.py，MIT License，Copyright (c) 2021 Nano；
其注释说明源自 https://gitee.com/FancyCabbage/skyland-auto-sign）。MIT 许可全文：

    Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
    documentation files (the "Software"), to deal in the Software without restriction, including without limitation
    the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
    permit persons to whom the Software is furnished to do so, subject to the following conditions:
    The above copyright notice and this permission notice shall be included in all copies or substantial portions of
    the Software.
    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
    WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
    COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
    OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

要 `cryptography`（TripleDES / AES / RSA）。网络请求经 maapilot.crew.skland 的 http()，测试里换掉。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import time
import uuid

DEVICE_URL = "https://fp-it.portal101.cn/deviceprofile/v4"
ORGANIZATION = "UWXspnCCJN4sfYlNfqps"
APP_ID = "default"
PUBLIC_KEY = ("MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCmxMNr7n8ZeT0tE1R9j/mPixoinPkeM+k4VGIn/s0k7N5rJAfnZ0eMER+Qhw"
              "Fvshzo0LNmeUkpR8uIlU/GEVr8mN28sKmwd2gpygqj0ePnBmOW4v0ZVwbSYK+izkhVFk2V/doLoMbWy6b+UnA8mkjvg0iYWRByfRs"
              "K2gdl7llqCwIDAQAB")

# 字段 → (DES 密钥或 None 表示不加密, 混淆后的名字)
DES_RULE = {
    "appId": ("uy7mzc4h", "xx"), "box": (None, "jf"), "canvas": ("snrn887t", "yk"),
    "clientSize": ("cpmjjgsu", "zx"), "organization": ("78moqjfc", "dp"), "os": ("je6vk6t4", "pj"),
    "platform": ("pakxhcd2", "gm"), "plugins": ("v51m3pzl", "kq"), "pmf": ("2mdeslu3", "vw"),
    "protocol": (None, "protocol"), "referer": ("y7bmrjlc", "ab"), "res": ("whxqm2a7", "hf"),
    "rtype": ("x8o2h2bl", "lo"), "sdkver": ("9q3dcxp2", "sc"), "status": ("2jbrxxw4", "an"),
    "subVersion": ("eo3i2puh", "ns"), "svm": ("fzj3kaeh", "qr"), "time": ("q2t3odsk", "nb"),
    "timezone": ("1uv05lj5", "as"), "tn": ("x9nzj1bp", "py"), "trees": ("acfs0xo4", "pi"),
    "ua": ("k92crp1t", "bj"), "url": ("y95hjkoo", "cf"), "version": (None, "version"), "vpw": ("r9924ab5", "ca"),
}

BROWSER_ENV = {
    "plugins": "MicrosoftEdgePDFPluginPortableDocumentFormatinternal-pdf-viewer1,"
               "MicrosoftEdgePDFViewermhjfbmdgcfjbbpaeojofohoefgiehjai1",
    "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 "
          "Safari/537.36 Edg/129.0.0.0",
    "canvas": "259ffe69", "timezone": -480, "platform": "Win32", "url": "https://www.skland.com/", "referer": "",
    "res": "1920_1080_24_1.25", "clientSize": "0_0_1080_1920_1920_1080_1920_1080", "status": "0011",
}


class DeviceIdError(RuntimeError):
    pass


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        try:
            from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
        except ImportError:                                  # 老版本 cryptography
            TripleDES = algorithms.TripleDES
    except ImportError as e:
        raise DeviceIdError("缺少 Python 包 cryptography（项目运行环境要更新：uv pip install -e .）") from e
    return serialization, padding, Cipher, algorithms, modes, TripleDES


def des_field(value, key: str) -> str:
    """一个字段的 DES（单密钥 = 三份同样的 TripleDES 密钥，ECB）：字符串化、补 0 到 8 的倍数，base64。"""
    _, _, Cipher, _, modes, TripleDES = _crypto()
    data = str(value).encode("utf-8")
    data += b"\x00" * (-len(data) % 8)
    enc = Cipher(TripleDES((key * 3).encode("utf-8")), modes.ECB()).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode("utf-8")


def des_obfuscate(o: dict) -> dict:
    out = {}
    for k, v in o.items():
        rule = DES_RULE.get(k)
        if rule is None:
            out[k] = v
        else:
            key, name = rule
            out[name] = des_field(v, key) if key else v
    return out


def aes_hex(data: bytes, key: bytes) -> str:
    """AES-CBC（iv 0102030405060708），补 0 到 16 的倍数（至少补一个），十六进制。"""
    _, _, Cipher, algorithms, modes, _ = _crypto()
    data += b"\x00"
    data += b"\x00" * (-len(data) % 16)
    enc = Cipher(algorithms.AES(key), modes.CBC(b"0102030405060708")).encryptor()
    return (enc.update(data) + enc.finalize()).hex()


def gzip_b64(o: dict) -> bytes:
    return base64.b64encode(gzip.compress(json.dumps(o, ensure_ascii=False).encode("utf-8"), 2, mtime=0))


def tn(o: dict) -> str:
    """键排序，值拼起来：数字乘 10000，子对象递归。"""
    parts = []
    for k in sorted(o):
        v = o[k]
        if isinstance(v, (int, float)):
            v = str(v * 10000)
        elif isinstance(v, dict):
            v = tn(v)
        parts.append(v)
    return "".join(parts)


def smid() -> str:
    t = time.strftime("%Y%m%d%H%M%S", time.localtime())
    v = t + hashlib.md5(str(uuid.uuid4()).encode("utf-8")).hexdigest() + "00"
    return v + hashlib.md5(("smsk_web_" + v).encode("utf-8")).hexdigest()[:14] + "0"


def request_body() -> dict:
    serialization, padding, *_ = _crypto()
    uid = str(uuid.uuid4()).encode("utf-8")
    pri = hashlib.md5(uid).hexdigest()[:16]
    pk = serialization.load_der_public_key(base64.b64decode(PUBLIC_KEY))
    ep = base64.b64encode(pk.encrypt(uid, padding.PKCS1v15())).decode("utf-8")
    now_ms = int(time.time() * 1000)
    target = {**BROWSER_ENV, "vpw": str(uuid.uuid4()), "svm": now_ms, "trees": str(uuid.uuid4()), "pmf": now_ms,
              "protocol": 102, "organization": ORGANIZATION, "appId": APP_ID, "os": "web", "version": "3.0.0",
              "sdkver": "3.0.0", "box": "", "rtype": "all", "smid": smid(), "subVersion": "1.0.0", "time": 0}
    target["tn"] = hashlib.md5(tn(target).encode("utf-8")).hexdigest()
    return {"appId": APP_ID, "compress": 2, "data": aes_hex(gzip_b64(des_obfuscate(target)), pri.encode("utf-8")),
            "encode": 5, "ep": ep, "organization": ORGANIZATION, "os": "web"}


def device_id(http) -> str:
    """http(method, url, headers, body) → (status, headers, json)。成功 → "B" + deviceId。"""
    _, _, resp = http("POST", DEVICE_URL, {}, request_body())
    if not isinstance(resp, dict) or resp.get("code") != 1100 or not (resp.get("detail") or {}).get("deviceId"):
        raise DeviceIdError(f"数美设备号没拿到（code {resp.get('code') if isinstance(resp, dict) else '?'}）")
    return "B" + resp["detail"]["deviceId"]
