# auto_verify.py
import asyncio, sqlite3, uuid, jwt, requests, time
from datetime import datetime

async def verify_loop():
    from config.settings import get_settings
    s  = get_settings()
    ak = getattr(getattr(s,'api',None),'access_key',None)
    sk = getattr(getattr(s,'api',None),'secret_key',None)

    while True:
        print(f'\n{"="*50}')
        print(f'[{datetime.now().strftime("%H:%M:%S")}] 자동 검증')
        print(f'{"="*50}')

        # 업비트 실잔고
        payload = {'access_key': ak, 'nonce': str(uuid.uuid4())}
        token   = jwt.encode(payload, sk, algorithm='HS256')
        if isinstance(token, bytes): token = token.decode()
        balances = requests.get(
            'https://api.upbit.com/v1/accounts',
            headers={'Authorization': f'Bearer {token}'}, timeout=10
        ).json()
        upbit_map = {
            b['currency']: float(b.get('balance',0)) + float(b.get('locked',0))
            for b in balances if b.get('currency') != 'KRW'
        }
        krw = next((float(b.get('balance',0)) for b in balances if b['currency']=='KRW'), 0)

        # DB 포지션
        con = sqlite3.connect('database/apex_bot.db')
        cur = con.cursor()
        cur.execute('SELECT market, entry_price, volume, amount_krw FROM positions')
        db_positions = cur.fetchall()

        # 최근 SELL 내역
        cur.execute("""
            SELECT market, price, volume, amount_krw, timestamp
            FROM trade_history
            WHERE side='SELL' AND mode='live'
            ORDER BY timestamp DESC LIMIT 5
        """)
        recent_sells = cur.fetchall()
        con.close()

        # 교차검증
        print(f'  KRW 잔고: ₩{krw:,.0f}')
        print(f'\n  포지션 교차검증:')
        all_ok = True
        for mkt, entry, vol, amt in db_positions:
            coin = mkt.replace('KRW-','')
            real = upbit_map.get(coin, 0)
            diff = abs(real - vol)
            status = '✅' if diff < 0.01 else '❌'
            if diff >= 0.01: all_ok = False
            print(f'  {status} {mkt}: DB={vol:.4f} | 실잔고={real:.4f} | 차이={diff:.4f}')

        if not db_positions:
            print('  포지션 없음 (전량 청산 완료)')

        print(f'\n  최근 SELL 5건:')
        for mkt, price, vol, amt, ts in recent_sells:
            pnl = '확인필요'
            print(f'  {mkt} | ₩{price:.2f} × {vol:.4f} = ₩{amt:,.0f} | {ts[:19]}')

        if all_ok:
            print('\n  ✅ DB-실잔고 일치 확인')
        else:
            print('\n  ❌ 불일치 발견 → 즉시 확인 필요')

        # 5분 대기
        print(f'\n  다음 검증: 5분 후...')
        await asyncio.sleep(300)

asyncio.run(verify_loop())
