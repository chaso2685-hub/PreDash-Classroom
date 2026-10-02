import unittest
from unittest.mock import patch
from types import SimpleNamespace
import requests
from predash.nh import NH, BrokerError

def response(data, headers=None, status=200):
    return SimpleNamespace(status_code=status, headers=headers or {}, json=lambda: data)

def row(code='A005930', qty=2):
    return dict(iem_cd=code, iem_nm='삼성전자', itg_bnc_qty=qty,
                phs_pr=50000, now_pr=60000, eal_amt=qty*60000, eal_pls_amt=qty*10000)

class NHTests(unittest.TestCase):
    def client(self):
        c=NH(settings=dict(key='key',secret='secret',cano='12345678901',mode='real'))
        c.last_call=-100
        return c

    def test_auth_headers_and_multi_page_lots(self):
        replies=[response(dict(access_token='token',expires_in=86400)),
            response(dict(Output_1=[row()],rsp_cd='00165'),dict(cts='next',cts_flag='Y')),
            response(dict(Output_0=dict(dca=100000),Output_1=[row(qty=3)]),dict(cts_flag='N'))]
        with patch('predash.nh.requests.post',create=True,side_effect=replies) as post, patch('predash.nh.time.sleep'):
            c=self.client()
            snap=c.balance()
            self.assertEqual(snap['positions'][0]['quantity'],5)
            self.assertEqual(snap['positions'][0]['code'],'005930')
            self.assertEqual(snap['value'],300000)
            self.assertEqual(snap['cash'],100000)
            self.assertEqual(snap['positions'][0]['weight'],100)
            auth=post.call_args_list[0]
            self.assertEqual(auth.kwargs['params']['appsecretkey'],'secret')
            self.assertEqual(post.call_args_list[2].kwargs['headers']['cts'],'next')
            self.assertEqual(post.call_args_list[1].kwargs['json']['Input_0']['aly_qut_cd'],'1')
            c.authorize()
            self.assertEqual(post.call_count,3)

    def test_accounts_exclude_mock_and_unknown(self):
        c=self.client();c.token='token';c.expires=10**12
        with patch('predash.nh.requests.post',create=True,return_value=response(dict(Output_0=[
            dict(acct_no='12345678901',acct_type='01'),
            dict(acct_no='22222222222',acct_type='03'),
            dict(acct_no='33333333333',acct_type='unknown')]))):
            self.assertEqual(c.accounts(),['12345678901'])

    def test_missing_summary_and_business_error_do_not_become_zero(self):
        for data in ({'Output_1':[]}, {'rsp_msg':'조회 실패','Output_0':{'dca':0}}):
            c=self.client();c.token='token';c.expires=10**12
            with patch('predash.nh.requests.post',create=True,return_value=response(data)), self.assertRaises(BrokerError):
                c.balance()

    def test_repeated_continuation_fails(self):
        c=self.client();c.token='token';c.expires=10**12
        reply=response(dict(Output_0={'dca':0},Output_1=[]),dict(cts='same',cts_flag='Y'))
        with patch('predash.nh.requests.post',create=True,return_value=reply), patch('predash.nh.time.sleep'), self.assertRaises(BrokerError):
            c.balance()

    def test_secrets_never_in_network_error(self):
        with patch('predash.nh.requests.post',create=True,side_effect=requests.RequestException('secret key token')):
            with self.assertRaises(BrokerError) as e:
                self.client().authorize()
        self.assertNotIn('secret',str(e.exception))
        self.assertNotIn('token',str(e.exception))

    def test_session_clients_are_isolated_and_orders_disallowed(self):
        a=self.client();b=self.client();a.token='a'
        self.assertIsNone(b.token)
        with self.assertRaises(BrokerError):a.request('/krstock/order/v1/cashBuy')
        with self.assertRaises(BrokerError):a.fills()
        with self.assertRaises(BrokerError):NH(settings=dict(key='x',secret='y',mode='demo'))

if __name__=='__main__':
    unittest.main()

