"""Session-local, read-only Namuh PLUG adapter. No SDK globals or disk token cache."""
import math
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from predash.kis import BrokerError

BASE = 'https://api.nhplug.com:8443'
PATHS = {'/n2/acctinfo', '/krstock/inquiry/v1/balance'}

def numeric(value):
    try:
        n = float(str(value).replace(',', ''))
        if not math.isfinite(n):
            raise ValueError
        return n
    except (ValueError, TypeError):
        raise BrokerError('NH 잔고의 숫자 형식을 확인하지 못했습니다.') from None

def validate_payload(data):
    if not isinstance(data, dict):
        raise BrokerError('NH 응답 형식을 확인하지 못했습니다.')
    message = data.get('message') or {}
    if not isinstance(message, dict):
        raise BrokerError('NH 응답 메시지 형식을 확인하지 못했습니다.')
    # Business codes vary by operation. Do not treat HTTP 200 alone as success.
    text = str(data.get('rsp_msg', '')) + ' ' + str(message.get('usr_msg', ''))
    if data.get('error_code') or str(message.get('msg_lv_code', '')).upper() in ('E', 'F') or re.search(
        r'실패|오류|불가|거부|유효하지|권한.*없|잘못|초과|만료|error|fail|invalid', text, re.I
    ):
        code = str(data.get('error_code') or data.get('rsp_cd') or message.get('msg_code') or '')
        safe = code if re.fullmatch(r'[A-Za-z0-9_]{1,24}', code) else '미확인'
        raise BrokerError(f'NH 조회 거부 · 코드 {safe}. 키·계좌 조회 권한을 확인하세요.')
    return data

class NH:
    def __init__(self, *, settings):
        self.key = settings.get('key', '')
        self.secret = settings.get('secret', '')
        self.cano = settings.get('cano', '')
        self.product = ''
        self.mode = settings.get('mode', 'real')
        if not self.key or not self.secret:
            raise BrokerError('나무 PLUG App Key와 App Secret을 입력하세요.')
        if self.mode != 'real':
            raise BrokerError('현재 NH 연결은 실전 잔고 조회만 지원합니다.')
        self.token = None
        self.expires = 0
        self.last_call = 0

    def request(self, path, **kwargs):
        # Fixed HTTPS host and explicit read-only allowlist.
        if path not in PATHS | {'/oauth2/token'}:
            raise BrokerError('지원하지 않는 NH 조회 기능입니다.')
        delay = .3 - (time.monotonic() - self.last_call)
        if delay > 0:
            time.sleep(delay)
        self.last_call = time.monotonic()
        try:
            response = requests.post(BASE + path, timeout=(5, 20), allow_redirects=False, **kwargs)
            if response.status_code == 429:
                raise BrokerError('NH 호출 제한 · 잠시 후 다시 조회하세요.')
            if response.status_code != 200:
                raise BrokerError(f'NH 연결 실패 (HTTP {response.status_code}) · 키와 서비스 상태를 확인하세요.')
            return response, validate_payload(response.json())
        except (requests.RequestException, ValueError):
            # Never print requests exceptions: token URL contains credentials.
            raise BrokerError('NH 연결 실패 · 네트워크와 서비스 상태를 확인하세요.') from None

    def authorize(self):
        if self.token and time.time() < self.expires:
            return
        _, data = self.request('/oauth2/token',
            params={'appkey': self.key, 'appsecretkey': self.secret,
                    'grant_type': 'client_credentials', 'scope': 'oob'},
            headers={'content-type': 'application/x-www-form-urlencoded'})
        if not isinstance(data.get('access_token'), str) or not data['access_token']:
            raise BrokerError('NH 인증 토큰을 발급받지 못했습니다.')
        self.token = data['access_token']
        self.expires = time.time() + max(0, numeric(data.get('expires_in', 86400)) - 120)

    def pages(self, path, params):
        self.authorize()
        cts = flag = ''
        seen = set()
        for _ in range(100):
            headers = {'authorization': 'Bearer ' + self.token,
                       'x-client-id': self.key, 'x-client-secret': self.secret,
                       'content-type': 'application/json; charset=UTF-8'}
            if cts:
                headers['cts'] = cts
            if flag:
                headers['cts_flag'] = flag
            response, data = self.request(path, headers=headers, json={'Input_0': params})
            yield data
            cts = response.headers.get('cts', '').strip()
            flag = response.headers.get('cts_flag', '').strip().upper()
            more = flag == 'Y' or (not flag and str(data.get('rsp_cd')) in ('00165', '00218'))
            if not more or flag == 'N':
                return
            if not cts or cts in seen:
                raise BrokerError('NH 다음 페이지를 확인하지 못해 전체 조회를 중단했습니다.')
            seen.add(cts)
        raise BrokerError('NH 조회 페이지 한도를 초과했습니다.')

    def accounts(self):
        rows = []
        for data in self.pages('/n2/acctinfo', {}):
            block = data.get('Output_0')
            if not isinstance(block, list):
                raise BrokerError('NH 계좌 목록을 확인하지 못했습니다.')
            for row in block:
                if not isinstance(row, dict):
                    raise BrokerError('NH 계좌 목록 형식을 확인하지 못했습니다.')
                if row.get('acct_type') not in ('01', '02'):
                    continue
                account = str(row.get('acct_no', '')).strip()
                if not re.fullmatch(r'\d{11}', account):
                    raise BrokerError('NH 계좌번호 형식을 확인하지 못했습니다.')
                if account not in rows:
                    rows.append(account)
        if not rows:
            raise BrokerError('조회 가능한 NH 실전 계좌가 없습니다. PLUG 계좌 등록을 확인하세요.')
        return rows

    def balance(self):
        if not re.fullmatch(r'\d{11}', self.cano):
            raise BrokerError('NH 계좌 목록에서 계좌를 선택하세요.')
        positions = {}
        summary = {}
        for data in self.pages('/krstock/inquiry/v1/balance',
            {'act_no': self.cano, 'bnc_bse_cd': '5', 'ltg_aot_dit_cd': '9',
             'aet_bse': '2', 'qut_dit_cd': 'UNT', 'aly_qut_cd': '1'}):
            current = data.get('Output_0', {})
            rows = data.get('Output_1', [])
            if not isinstance(current, dict) or not isinstance(rows, list):
                raise BrokerError('NH 잔고 응답 형식을 확인하지 못했습니다.')
            summary.update(current)
            for row in rows:
                if not isinstance(row, dict):
                    raise BrokerError('NH 보유종목 응답 형식을 확인하지 못했습니다.')
                quantity = numeric(row.get('itg_bnc_qty'))
                if quantity <= 0:
                    continue
                code = str(row.get('iem_cd', '')).strip()
                if re.fullmatch(r'A\d{6}', code):
                    code = code[1:]
                if not code:
                    raise BrokerError('NH 잔고의 종목코드를 확인하지 못했습니다.')
                value, pnl = numeric(row.get('eal_amt')), numeric(row.get('eal_pls_amt'))
                cost, price = numeric(row.get('phs_pr')), numeric(row.get('now_pr'))
                if code in positions:
                    old = positions[code]
                    old['average_cost'] = (old['average_cost'] * old['quantity'] + cost * quantity) / (old['quantity'] + quantity)
                    old['quantity'] += quantity
                    old['value'] += value
                    old['pnl'] += pnl
                else:
                    positions[code] = dict(code=code, name=str(row.get('iem_nm') or code),
                        quantity=quantity, average_cost=cost, price=price, value=value, pnl=pnl)
        # Missing blocks can mean no data; never show an unverified zero balance.
        if not summary or summary.get('dca') in (None, ''):
            raise BrokerError('NH 잔고 집계가 없어 조회 결과를 확정할 수 없습니다.')
        rows = list(positions.values())
        total = sum(p['value'] for p in rows)
        for p in rows:
            p['weight'] = p['value'] / total * 100 if total > 0 else 0
        return dict(positions=rows, value=total, pnl=sum(p['pnl'] for p in rows),
            cash=numeric(summary['dca']), mode='real', broker='nh',
            fetched=datetime.now(ZoneInfo('Asia/Seoul')).isoformat())

    def unsupported(self, *args, **kwargs):
        raise BrokerError('NH 연결은 현재 국내주식 잔고 조회를 지원합니다. 체결·수급·지수 분석은 아직 지원하지 않습니다.')

    fills = investor_flow = index_bars = market_flow = daily_bars = unsupported

