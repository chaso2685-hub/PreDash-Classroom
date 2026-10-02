"""Classroom credentials are held only in the current Streamlit session."""
import streamlit as st
from predash.kis import KIS, BrokerError
from predash.nh import NH


def account_settings(mode=None):
    settings = st.session_state.get('classroom_credentials', {})
    selected = mode or settings.get('mode', 'demo')
    if selected != settings.get('mode'):
        return dict(mode=selected, key='', secret='', cano='', product='')
    return dict(mode=selected, broker=settings.get('broker', 'kis'), **{k: settings.get(k, '') for k in ('key','secret','cano','product')})


def connection_form():
    if st.session_state.pop('classroom_clear_inputs', False):
        for key in ('class_key','class_secret','class_cano','class_product'):
            st.session_state.pop(key, None)
    st.subheader('내 증권사 계좌 연결')
    st.caption('각자 Fork한 앱에서 본인 키를 입력하세요. 현재 접속 세션에서만 사용합니다.')
    if st.session_state.get('classroom_credentials'):
        st.success(broker_name() + ' 계좌 연결됨 · ' + ('모의투자' if account_settings()['mode']=='demo' else '실전 조회'))
        if st.button('계좌 연결 해제'):
            authorized = st.session_state.get('authorized')
            st.session_state.clear()
            if authorized: st.session_state.authorized = True
            st.rerun()
        return
    broker = st.selectbox('증권사', ['NH투자증권 · 나무 PLUG', '한국투자증권'], key='class_broker')
    if broker.startswith('NH'):
        nh_connection_form()
        return
    # Clear pending NH authentication when switching providers.
    st.session_state.pop('_nh_pending', None)
    st.session_state.pop('_nh_accounts', None)
    with st.form('classroom_connection'):
        mode = st.radio('투자 환경', ['모의투자','실전 조회'], horizontal=True)
        key = st.text_input('App Key', type='password', key='class_key')
        secret = st.text_input('App Secret', type='password', key='class_secret')
        cano = st.text_input('계좌번호 앞 8자리', type='password', max_chars=8, key='class_cano')
        product = st.text_input('계좌번호 뒤 2자리', max_chars=2, key='class_product')
        submitted = st.form_submit_button('연결 확인', type='primary', use_container_width=True)
    if submitted:
        settings = dict(mode='demo' if mode=='모의투자' else 'real',key=key.strip(),secret=secret.strip(),cano=cano.strip(),product=product.strip())
        try:
            client = KIS(settings=settings)
            with st.spinner('잔고 조회 권한을 확인합니다…'):
                client.balance()
            st.session_state.classroom_credentials = settings
            st.session_state['_kis_client_demo' if settings['mode']=='demo' else '_kis_client'] = client
            st.session_state.classroom_clear_inputs = True
            st.rerun()
        except BrokerError as error:
            st.error(str(error))
    st.info('연결 해제와 로그아웃은 키·잔고·접속 중 실습 기록을 지웁니다. 필요한 기록은 먼저 백업하세요.')



def broker_name():
    return 'NH투자증권 · 나무 PLUG' if account_settings().get('broker') == 'nh' else '한국투자증권'


def account_connected(mode=None):
    settings = account_settings(mode)
    if settings.get('broker') == 'nh':
        return all(settings.get(k) for k in ('key', 'secret', 'cano'))
    return all(settings.get(k) for k in ('key', 'secret', 'cano', 'product'))


def nh_connection_form():
    st.caption('나무 PLUG 실전 계좌 · 국내주식 잔고 조회 전용')
    st.info('NH 체결내역·수급·지수 분석과 NH 모의계좌는 아직 지원하지 않습니다.')
    if st.session_state.pop('_nh_clear_inputs', False):
        for key in ('nh_key', 'nh_secret'):
            st.session_state.pop(key, None)
    with st.form('nh_authentication'):
        key = st.text_input('나무 PLUG App Key', type='password', key='nh_key')
        secret = st.text_input('나무 PLUG App Secret', type='password', key='nh_secret')
        submitted = st.form_submit_button('NH 인증 · 계좌 목록 조회', type='primary', use_container_width=True)
    if submitted:
        # A failed new authentication cannot reuse a previous account list.
        st.session_state.pop('_nh_pending', None)
        st.session_state.pop('_nh_accounts', None)
        st.session_state.pop('nh_account_choice', None)
        try:
            client = NH(settings=dict(key=key.strip(), secret=secret.strip(), mode='real'))
            with st.spinner('나무 PLUG 계좌 목록을 조회합니다…'):
                accounts = client.accounts()
            st.session_state['_nh_pending'] = client
            st.session_state['_nh_accounts'] = accounts
            st.session_state['_nh_clear_inputs'] = True
            st.rerun()
        except BrokerError as error:
            st.error(str(error))
    client = st.session_state.get('_nh_pending')
    accounts = st.session_state.get('_nh_accounts', [])
    if client and accounts:
        st.success('NH 인증 완료 · 연결할 계좌를 선택하세요.')
        with st.form('nh_select_account'):
            account = st.selectbox('NH 계좌', accounts,
                format_func=lambda n: '*' * (len(n)-4) + n[-4:], key='nh_account_choice')
            connect = st.form_submit_button('NH 잔고 조회 · 연결 확인', type='primary', use_container_width=True)
        if connect:
            try:
                client.cano = account
                with st.spinner('선택한 NH 계좌의 국내주식 잔고를 조회합니다…'):
                    snapshot = client.balance()
                st.session_state.classroom_credentials = dict(
                    broker='nh', mode='real', key=client.key, secret=client.secret, cano=account, product='')
                st.session_state['_nh_client'] = client
                st.session_state.snapshot = snapshot
                st.session_state.pop('_nh_pending', None)
                st.session_state.pop('_nh_accounts', None)
                st.session_state.pop('nh_account_choice', None)
                st.rerun()
            except BrokerError as error:
                st.error(str(error))
        if st.button('NH 인증 취소'):
            for key in ('_nh_pending', '_nh_accounts', 'nh_account_choice', 'nh_key', 'nh_secret'):
                st.session_state.pop(key, None)
            st.rerun()
    st.link_button('나무 PLUG 사용 안내', 'https://www.nhplug.com/howto-use')
    st.caption('키·토큰·계좌번호는 현재 세션에서만 사용합니다. 연결 해제·로그아웃 시 삭제됩니다.')
