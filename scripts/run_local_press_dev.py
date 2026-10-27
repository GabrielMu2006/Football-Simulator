"""Launch the current source UI with an explicit V0.3.0 development data root."""
from pathlib import Path
import argparse,os,subprocess,sys

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--data-root',type=Path,required=True)
args=parser.parse_args()
root=args.data_root.expanduser().resolve()
# An explicit path is required on every launch; the stable roots are never a
# fallback. Further save controls remain the existing application's controls.
protected=[Path.home()/'Library/Application Support'/name for name in
           ('Football Simulator','Football Simulator V0.2.0')]
if any(root==p or p in root.parents for p in protected) or 'release' in root.parts:
    parser.error('请选择独立开发数据目录。')
env={**os.environ,'FS_DATA_ROOT':str(root),'FS_PRESS_DEV':'1'}
raise SystemExit(subprocess.call([sys.executable,str(ROOT/'ui_v2_main.py')],cwd=ROOT,env=env))
