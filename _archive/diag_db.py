import asyncio, sys
sys.path.insert(0, '.')

async def test():
    from core.portfolio_manager import PortfolioManager
    from config.settings import Settings

    settings = Settings()
    pm = PortfolioManager()
    print('1) 초기 position_count:', pm.position_count)

    import aiosqlite
    from pathlib import Path
    db_path = Path('database/apex_bot.db')

    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT b.market, b.price, b.volume, b.amount_krw, b.strategy
            FROM trade_history b
            LEFT JOIN trade_history s
                ON b.market = s.market
               AND s.side = 'SELL'
               AND s.timestamp > b.timestamp
            WHERE b.side = 'BUY'
              AND b.mode = 'paper'
              AND s.id IS NULL
            ORDER BY b.timestamp ASC
        """)
        rows = await cur.fetchall()

    print('2) DB 미체결 BUY rows:', len(rows))
    for r in rows:
        mkt = r['market']
        price = r['price']
        vol = r['volume']
        print('  ', mkt, '| price=', price, '| vol=', vol)
        if not pm.is_position_open(mkt):
            pm.open_position(mkt, price, vol,
                             r['amount_krw'],
                             r['strategy'] or 'unknown',
                             price * 0.97,
                             price * 1.05)

    print('3) 복원 후 position_count:', pm.position_count)
    print('4) 복원된 포지션:', list(pm.open_positions.keys()))

    markets = settings.trading.target_markets
    existing = [m for m in markets if pm.is_position_open(m)]
    print('5) existing_markets:', existing)

asyncio.run(test())
