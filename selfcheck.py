# -*- coding: utf-8 -*-
"""
安装自检：确认路径、依赖、CUDA 都能用。setup.ps1 会调用它。
退出码 0 = 全部通过；非 0 = 有未通过项（逐条打印 MISS/!! 提示）。
"""
import os
import sys

PKG = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PKG, 'audiobook_tool'))

bad = 0

try:
    import config
except Exception as e:                                   # noqa: BLE001
    print(f'  MISS 无法导入 config: {e}')
    sys.exit(1)

print('  --- 路径解析 ---')
for k in ('MODEL_DIR', 'DEFAULT_REF_AUDIO', 'CAMPPLUS_ONNX', 'FFMPEG', 'FFPROBE'):
    v = getattr(config, k)
    ok = os.path.exists(v)
    bad += 0 if ok else 1
    print(f"  {'OK  ' if ok else 'MISS'} {k:<18} {v}")
print(f'  （包根 PACKAGE_ROOT = {config.PACKAGE_ROOT}）')

print('  --- 运行环境 ---')
try:
    import torch
    cuda = torch.cuda.is_available()
    extra = f'  {torch.cuda.get_device_name(0)}' if cuda else ''
    print(f"  {'OK  ' if cuda else '!!  '} torch {torch.__version__}  cuda={cuda}{extra}")
    if not cuda:
        bad += 1
        print('       需要 NVIDIA 显卡 + 对应驱动；CPU 也能跑但会非常慢')
except Exception as e:                                   # noqa: BLE001
    bad += 1
    print(f'  MISS torch: {e}')

for m in ('torchaudio', 'transformers', 'librosa', 'soundfile',
          'onnxruntime', 'qwen_tts', 'modelscope'):
    # 注：gradio 已弃用（旧界面归档为 _legacy_gradio.py），2026-10-14 由打包 Bot 移出本清单
    try:
        mod = __import__(m)
        ver = getattr(mod, '__version__', '')
        print(f'  OK   import {m} {ver}')
    except Exception as e:                               # noqa: BLE001
        bad += 1
        print(f'  MISS import {m}: {e}')

print('  --- 关键依赖版本约束 ---')
try:
    import transformers
    want = '4.57.3'
    ok = transformers.__version__ == want
    print(f"  {'OK  ' if ok else 'WARN'} transformers={transformers.__version__} (应为 {want}，"
          f"qwen_tts 0.1.1 强依赖)")
    if not ok:
        print('       装错版本会导致 qwen_tts 调用失败')
except Exception:                                        # noqa: BLE001
    pass

if bad:
    print(f'\n自检结果: {bad} 项未通过（见上面 MISS / !! / WARN）')
sys.exit(1 if bad else 0)
