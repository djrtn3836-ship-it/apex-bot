import asyncio, sys
sys.path.insert(0, '.')

async def test():
    from data.storage.cache_manager import CacheManager
    from data.collectors.rest_collector import RestCollector
    from config.settings import Settings

    settings = Settings()
    cm = CacheManager()

    markets = ['KRW-BTC', 'KRW-ADA', 'KRW-DOGE']

    print("=== NpyCache   ===")
    npy = getattr(cm, '_npy_cache', None)
    print('_npy_cache:', npy)

    for m in markets:
        df = cm.get_ohlcv(m, '1h')
        if df is not None:
            print(f'{m}: {len(df)}개 캔들 (get_ohlcv)')
        else:
            print(f'{m}: None (get_ohlcv)')

    print()
    print("=== REST    ===")
    rc = RestCollector()
    for m in markets:
        try:
            df2 = await rc.get_ohlcv(m, interval='1h', count=30)
            if df2 is not None:
                print(f'{m}: REST {len(df2)}개 캔들')
            else:
                print(f'{m}: REST None')
        except Exception as e:
            print(f'{m}: REST  - {e}')

asyncio.run(test())
