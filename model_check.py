import os, time

files = [
    'models/saved/ensemble_best.pt',
    'models/saved/ppo/best_model.zip',
    'models/saved/ensemble_weights.json',
]

for f in files:
    if os.path.exists(f):
        st = os.stat(f)
        mtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))
        size_mb = st.st_size / 1024 / 1024
        print(f'{f}')
        print(f'  최종 수정: {mtime}')
        print(f'  크기: {size_mb:.2f} MB')
    else:
        print(f'{f} -- 파일 없음')
    print()
