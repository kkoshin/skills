"""零依赖的币安 REST 客户端，支持 Ed25519 / HMAC-SHA256 签名。

设计取舍：
  * 纯标准库。Ed25519 用 RFC 8032 参考实现，避免依赖 cryptography / openssl 3.x
    （macOS 自带的是 LibreSSL，不支持 `pkeyutl -rawin`）。
  * 私钥全程只在内存里，不落盘 —— 早期版本用 openssl 子进程签名，必须先把密钥
    写成临时文件，对公开分发的工具来说这个代价不值得。
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://api.binance.com"


# --------------------------------------------------------------------------
# Ed25519 (RFC 8032 参考实现)
# --------------------------------------------------------------------------
_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493


def _modp_inv(x):
    return pow(x, _P - 2, _P)


_D = -121665 * _modp_inv(121666) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _sha512_modq(s):
    return int.from_bytes(hashlib.sha512(s).digest(), "little") % _Q


def _point_add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % _P
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % _P
    C = 2 * P[3] * Q[3] * _D % _P
    D = 2 * P[2] * Q[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _point_mul(s, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _point_add(Q, P)
        P = _point_add(P, P)
        s >>= 1
    return Q


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * _modp_inv(_D * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _I % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_G_Y = 4 * _modp_inv(5) % _P
_G = (_recover_x(_G_Y, 0), _G_Y, 1, _recover_x(_G_Y, 0) * _G_Y % _P)


def _point_compress(P):
    zinv = _modp_inv(P[2])
    x = P[0] * zinv % _P
    y = P[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def ed25519_sign(seed: bytes, msg: bytes) -> bytes:
    """seed 为 32 字节私钥种子, 返回 64 字节签名。"""
    if len(seed) != 32:
        raise ValueError(f"Ed25519 私钥种子必须是 32 字节, 实际 {len(seed)}")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    A = _point_compress(_point_mul(a, _G))
    r = _sha512_modq(h[32:] + msg)
    Rs = _point_compress(_point_mul(r, _G))
    k = _sha512_modq(Rs + A + msg)
    s = (r + k * a) % _Q
    return Rs + int.to_bytes(s, 32, "little")


# --------------------------------------------------------------------------
# 密钥解析
# --------------------------------------------------------------------------
def _strip_pem(text: str) -> str:
    """去掉 PEM 头尾和换行, 返回裸 base64。"""
    lines = [l.strip() for l in text.splitlines()
             if l.strip() and not l.strip().startswith("-----")]
    return "".join(lines)


def _ed25519_seed_from_der(der: bytes):
    """从 PKCS#8 DER 中取出 32 字节种子; 不是 Ed25519 则返回 None。

    Ed25519 PKCS#8 固定为:
      30 2e 02 01 00 30 05 06 03 2b 65 70 04 22 04 20 <32B seed>
    最后一个 0x04 0x20 之后的 32 字节就是种子。这里做结构校验而非直接切片。
    """
    if len(der) == 48 and der[:2] == b"\x30\x2e" and der[2:5] == b"\x02\x01\x00":
        if der[5:12] == b"\x30\x05\x06\x03\x2b\x65\x70":      # OID 1.3.101.112
            if der[12:16] == b"\x04\x22\x04\x20":
                return der[16:48]
    return None


class Credentials:
    """币安凭据。secret 支持三种形态, 自动识别:

      1. HMAC 密钥字符串      -> HMAC-SHA256, 签名为 hex
      2. Ed25519 私钥 (base64 PKCS#8, 带不带 PEM 头都行) -> Ed25519, 签名为 base64
      3. 指向 PEM/密钥文件的路径 -> 读文件后按上面两种再判断一次

    第 3 种是必须的: 官方 binance-cli 就是把 BINANCE_SECRET_KEY 当文件路径用的,
    两种约定会混用, 所以要两种都吃。
    """

    def __init__(self, api_key: str, secret: str):
        if not api_key:
            raise ValueError("缺少 API Key")
        self.api_key = api_key.strip()
        raw = secret.strip()

        path = os.path.expanduser(raw)
        if os.path.isfile(path):
            with open(path) as f:
                raw = f.read().strip()

        self.ed25519_seed = None
        self.hmac_secret = None

        body = _strip_pem(raw)
        try:
            der = base64.b64decode(body, validate=True)
        except Exception:
            der = None

        if der:
            seed = _ed25519_seed_from_der(der)
            if seed:
                self.ed25519_seed = seed
                return
            if der[:1] == b"\x30":
                raise ValueError(
                    "检测到 PKCS#8 私钥但不是 Ed25519（可能是 RSA）。"
                    "本工具只支持 HMAC 与 Ed25519。"
                )
        self.hmac_secret = raw

    @property
    def kind(self) -> str:
        return "ed25519" if self.ed25519_seed else "hmac"

    def sign(self, query: str) -> str:
        if self.ed25519_seed:
            return base64.b64encode(
                ed25519_sign(self.ed25519_seed, query.encode())
            ).decode()
        return hmac.new(self.hmac_secret.encode(), query.encode(),
                        hashlib.sha256).hexdigest()

    @classmethod
    def from_env(cls):
        key = os.environ.get("BINANCE_API_KEY", "")
        secret = os.environ.get("BINANCE_SECRET_KEY", "")
        if not key or not secret:
            raise SystemExit(
                "缺少环境变量 BINANCE_API_KEY / BINANCE_SECRET_KEY"
            )
        return cls(key, secret)


# --------------------------------------------------------------------------
# 请求
# --------------------------------------------------------------------------
class BinanceError(RuntimeError):
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload
        super().__init__(f"HTTP {status}: {json.dumps(payload, ensure_ascii=False)[:300]}")


class Client:
    # 本机到币安的连接偶发卡顿(实测有一次卡死 >3 分钟), 而币安默认窗口只有
    # 5000ms, 会让请求在到达时被判 "Timestamp outside of recvWindow"。
    # 放宽到 60s 后基本不再出现, 这是实测踩出来的, 别改小。
    DEFAULT_RECV_WINDOW = 60000

    def __init__(self, credentials=None, base=None):
        self.creds = credentials or Credentials.from_env()
        self.base = base or os.environ.get("BINANCE_API_BASE", DEFAULT_BASE)

    def request(self, method, path, params=None, signed=True, base=None,
                recv_window=None, timeout=30):
        params = dict(params or {})
        query = ""
        if signed:
            params["recvWindow"] = params.get("recvWindow",
                                              recv_window or self.DEFAULT_RECV_WINDOW)
            params["timestamp"] = int(time.time() * 1000)
            # 关键: 签名必须覆盖"实际发出的那个字符串"。先编码一次并固定下来,
            # 再把 signature 追加到同一个串上, 否则参数一多就会签名不匹配。
            query = urllib.parse.urlencode(sorted(params.items()))
            query += "&signature=" + urllib.parse.quote(self.creds.sign(query), safe="")
        elif params:
            query = urllib.parse.urlencode(params)

        url = f"{base or self.base}{path}" + (f"?{query}" if query else "")
        data = None
        if method.upper() != "GET" and signed:
            data = query.encode()
            url = f"{base or self.base}{path}"

        req = urllib.request.Request(url, method=method.upper(), data=data, headers={
            "X-MBX-APIKEY": self.creds.api_key,
            "Content-Type": "application/x-www-form-urlencoded",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode()
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"raw": raw[:500]}
            raise BinanceError(e.code, payload) from None

        # 币安对部分查询会返回 HTTP 200 + 空响应体（实测 /sapi/v1/equity/market/quote
        # 传不存在的 symbol 就是这样），既没有错误码也没有消息。直接 json.loads 会抛
        # JSONDecodeError 堆栈，对调用方毫无信息量，所以在这里转成明确的错误。
        if not body.strip():
            raise BinanceError(200, {
                "msg": f"接口返回空响应（HTTP 200 无内容）: {path}。"
                       f"常见原因是标的不存在、该标的无此市场数据，或参数不被接受。"})
        try:
            return json.loads(body)
        except ValueError:
            raise BinanceError(200, {
                "msg": f"接口返回了非 JSON 内容: {path}",
                "raw": body[:300]}) from None
