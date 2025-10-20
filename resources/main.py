# -*- coding: utf-8 -*-
# ============================================================
# Author: Meshal (Telegram: @i_Meshal)
# حفظ الحقوق: @i_Meshal
# Screen Rec. Plugin
# Developed by Meshal © 2025
# Licensed under MIT
# https://github.com/i-Meshal/Screen-Rec./
# ============================================================

import os, sys, time, errno, signal, shutil, subprocess, json, logging, urllib.parse, zipfile
from logging.handlers import RotatingFileHandler
import xbmc, xbmcgui, xbmcaddon, xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_NAME = ADDON.getAddonInfo('name')
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))
ADDON_PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
PID_FILE = os.path.join(ADDON_PROFILE, 'recording.pid')
STATUS_FILE = os.path.join(ADDON_PROFILE, 'recording_status.txt')
FFMPEG_LOG = os.path.join(ADDON_PROFILE, 'ffmpeg.log')
ERROR_LOG = os.path.join(ADDON_PROFILE, 'errors.log')

if not os.path.exists(ADDON_PROFILE):
    os.makedirs(ADDON_PROFILE, exist_ok=True)

_get_bool = lambda s, d=False: (s or '').strip().lower() in ('1','true','yes','on') if (s or '').strip() != '' else d
DEBUG_ON = _get_bool(ADDON.getSetting('debug_log'), False)

LOGGER = logging.getLogger(ADDON_ID)
LOGGER.setLevel(logging.DEBUG if DEBUG_ON else logging.INFO)
if not LOGGER.handlers:
    h = RotatingFileHandler(ERROR_LOG, maxBytes=512*1024, backupCount=3, encoding='utf-8')
    h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(funcName)s:%(lineno)d %(message)s'))
    LOGGER.addHandler(h)

# ---------- Helpers ----------
def _pid_is_running(pid):
    if not pid: return False
    try:
        os.kill(pid, 0)
    except OSError as e:
        return e.errno != errno.ESRCH
    else:
        return True

def save_status(is_recording, recording_file, pid=None):
    try:
        with open(STATUS_FILE,'w',encoding='utf-8') as f:
            f.write('1' if is_recording else '0')
            if recording_file:
                f.write('\n' + (recording_file or ''))
        if pid:
            with open(PID_FILE,'w',encoding='utf-8') as pf:
                pf.write(str(pid))
        else:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
    except Exception as e:
        LOGGER.error('save_status error: %s', e)

def load_status():
    is_rec=False; rec_file=None
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE,'r',encoding='utf-8') as pf:
                pid=int(pf.read().strip() or 0)
            if pid and _pid_is_running(pid):
                is_rec=True
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE,'r',encoding='utf-8') as f:
                lines=f.readlines()
                if lines:
                    is_rec = (lines[0].strip()=='1') or is_rec
                    if len(lines)>1:
                        rec_file=lines[1].strip()
    except Exception as e:
        LOGGER.error('load_status error: %s', e)
    return is_rec, rec_file

FFMPEG_CANDIDATES = ['/storage/.kodi/addons/tools.ffmpeg-tools/bin/ffmpeg','/usr/bin/ffmpeg','ffmpeg']

def get_ffmpeg_path():
    try:
        tools = xbmcaddon.Addon('tools.ffmpeg-tools')
        p = os.path.join(tools.getAddonInfo('path'),'bin','ffmpeg')
        if os.path.exists(p): return p
    except Exception:
        pass
    which = shutil.which('ffmpeg')
    if which: return which
    for p in FFMPEG_CANDIDATES:
        try:
            r = subprocess.run([p,'-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
            if r.returncode==0: return p
        except Exception:
            continue
    return None

# ---------- Build FFmpeg cmd ----------
def get_resolution(key):
    return {'0':'1280x720','1':'1920x1080','2':'3840x2160'}.get(key,'1280x720')

def get_fps(key):
    return {'0':'10','1':'15','2':'20','3':'25','4':'30'}.get(key,'25')

def get_quality(key):
    return {'0':'50','1':'40','2':'30'}.get(key,'40')

def read_encoder():
    try:
        idx=int(ADDON.getSetting('encoder') or '1')
    except:
        idx=1
    return ['vp9','x264','v4l2m2m'][idx if 0<=idx<=2 else 1]

def build_cmd(output_path):
    ffmpeg=get_ffmpeg_path()
    if not ffmpeg: return None, output_path

    res=get_resolution(ADDON.getSetting('resolution') or '1')
    fps=get_fps(ADDON.getSetting('framerate') or '3')
    q=get_quality(ADDON.getSetting('quality') or '1')
    enc=read_encoder()

    if enc in ('x264','v4l2m2m') and not output_path.endswith('.mp4'):
        output_path=os.path.splitext(output_path)[0]+'.mp4'
    elif enc=='vp9' and not output_path.endswith('.webm'):
        output_path=os.path.splitext(output_path)[0]+'.webm'

    try:
        w,h = (res.split('x')+['720'])[:2]
    except Exception:
        w,h='1280','720'

    vf=f"scale={w}:{h}:flags=bicubic,format=yuv420p"

    cmd=[ffmpeg]
    cmd += ['-loglevel','info','-stats'] if DEBUG_ON else ['-loglevel','warning']
    cmd += ['-fflags','+genpts','-f','fbdev','-framerate', fps, '-i','/dev/fb0','-vf', vf,'-y']

    if enc=='x264':
        cmd += ['-c:v','libx264','-preset','ultrafast','-tune','zerolatency','-crf','28']
    elif enc=='v4l2m2m':
        cmd += ['-c:v','h264_v4l2m2m','-b:v','4M']
    else:
        cmd += ['-c:v','libvpx-vp9','-crf', q,'-b:v','0','-deadline','realtime','-cpu-used','5']

    cmd.append(output_path)
    return cmd, output_path

# ---------- Recording ----------
def start_recording():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE,'r',encoding='utf-8') as pf:
                pid=int(pf.read().strip() or 0)
            if pid and _pid_is_running(pid):
                xbmcgui.Dialog().ok(ADDON_NAME,'يوجد تسجيل نشط بالفعل. أوقفه أولًا.')
                return False
            else:
                os.remove(PID_FILE)
        except Exception:
            pass

    if not get_ffmpeg_path():
        xbmcgui.Dialog().ok(ADDON_NAME,'لم يتم العثور على FFmpeg. ثبّت إضافة FFmpeg Tools.')
        return False

    save_path=xbmcvfs.translatePath(ADDON.getSetting('save_path') or '') or os.path.join(ADDON_PROFILE,'recordings')
    try:
        os.makedirs(save_path, exist_ok=True)
    except Exception as e:
        xbmcgui.Dialog().ok(ADDON_NAME, f'خطأ في إنشاء مجلد الحفظ: {e}')
        LOGGER.exception("mkdir save_path failed")
        return False

    ts=time.strftime('%Y%m%d_%H%M%S')
    outfile=os.path.join(save_path, f'recording_{ts}.webm')
    cmd,outfile=build_cmd(outfile)
    if not cmd:
        xbmcgui.Dialog().ok(ADDON_NAME,'تعذّر بناء أمر FFmpeg.')
        return False

    LOGGER.info("FFmpeg command: %s", ' '.join(cmd))
    try:
        log_handle=open(FFMPEG_LOG,'ab',buffering=0)
        proc=subprocess.Popen(cmd, stdout=log_handle, stderr=log_handle, stdin=subprocess.PIPE, start_new_session=True)
        save_status(True, cmd[-1], proc.pid)
        xbmcgui.Dialog().notification(ADDON_NAME,'بدأ التسجيل', xbmcgui.NOTIFICATION_INFO, 3000)
        return True
    except Exception as e:
        xbmcgui.Dialog().ok(ADDON_NAME, f'فشل بدء التسجيل: {e}')
        LOGGER.exception("start_recording failed")
        save_status(False, None)
        return False

def _graceful_stop(pid):
    try: os.kill(pid, signal.SIGINT)
    except Exception: pass
    for _ in range(10):
        if not _pid_is_running(pid): return
        time.sleep(0.2)
    try: os.kill(pid, signal.SIGKILL)
    except Exception: pass

def stop_recording():
    is_rec, rec_file = load_status()
    if not is_rec:
        xbmcgui.Dialog().notification(ADDON_NAME,'لا يوجد تسجيل نشط', xbmcgui.NOTIFICATION_INFO,1200)
        return None
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE,'r',encoding='utf-8') as pf:
                pid=int(pf.read().strip() or 0)
            if pid and _pid_is_running(pid):
                LOGGER.info("Stopping recording ... pid=%s", pid)
                _graceful_stop(pid)
                xbmcgui.Dialog().notification(ADDON_NAME,'توقف التسجيل', xbmcgui.NOTIFICATION_INFO,2000)
    except Exception:
        LOGGER.exception("stop_recording failed")
    finally:
        try:
            if os.path.exists(PID_FILE): os.remove(PID_FILE)
        except Exception: pass
        save_status(False, None)
    return rec_file

# ---------- Play ----------
def _as_file_url(p):
    if p.startswith('file://'): return p
    if os.path.isabs(p):
        return 'file://' + p
    return p

def play_file(path):
    try:
        LOGGER.info('Play requested: %s (isdir=%s exists=%s)', path, os.path.isdir(path), os.path.exists(path))
        if not path or not os.path.exists(path):
            xbmcgui.Dialog().ok(ADDON_NAME, 'تعذر تشغيل الملف: المسار غير موجود.')
            return
        if os.path.isdir(path):
            entries = [os.path.join(path, f) for f in os.listdir(path) if os.path.isfile(os.path.join(path,f))]
            if not entries:
                xbmcgui.Dialog().ok(ADDON_NAME, 'المجلد فارغ، لا يوجد ملف للتشغيل.')
                return
            entries.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            path = entries[0]
            LOGGER.info('Directory given; fallback to latest file: %s', path)
        xbmc.executebuiltin('Dialog.Close(all,true)')
        xbmc.sleep(100)
        xbmc.Player().play(path)
        xbmc.sleep(150)
        if not xbmc.Player().isPlaying():
            jpath = _as_file_url(path)
            payload = {"jsonrpc":"2.0","id":1,"method":"Player.Open","params":{"item":{"file": jpath}}}
            xbmc.executeJSONRPC(json.dumps(payload))
            LOGGER.info('Fallback Player.Open JSON-RPC used with %s', jpath)
    except Exception:
        LOGGER.exception('play_file failed')
        xbmcgui.Dialog().ok(ADDON_NAME, 'تعذر تشغيل الملف (راجع errors.log).')

# ---------- Share (Litterbox/Catbox) ----------
def _run_curl_cancellable(args, title, msg, max_time=45):
    base = ['curl','-4','-sS','--http1.1','--connect-timeout','6','--max-time',str(max_time)]
    cmd = base + args
    dp = xbmcgui.DialogProgress()
    dp.create(title, msg)
    rc = -1; out=''; err=''; cancelled=False
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        start = time.time()
        while True:
            try:
                o, e = proc.communicate(timeout=0.5)
                out += o or ''
                err += e or ''
                rc = proc.returncode
                break
            except subprocess.TimeoutExpired:
                elapsed = time.time()-start
                pct = min(5 + int((elapsed/max(1.0,float(max_time)))*90), 99)
                try: dp.update(pct)
                except Exception: pass
                if dp.iscanceled():
                    cancelled=True
                    try:
                        proc.terminate()
                        try:
                            proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                    except Exception:
                        pass
                    rc = -15
                    err += '\nCancelled by user.'
                    break
        return rc, (out or '').strip(), (err or '').strip(), cancelled
    finally:
        try: dp.close()
        except Exception: pass

def _build_upload_cmd(video_file, backend):
    if backend == 'catbox':
        return ['-F','reqtype=fileupload','-F', f'fileToUpload=@{video_file}', 'https://catbox.moe/user/api.php']
    # default: Litterbox 72h
    return ['-F','reqtype=fileupload','-F','time=72h','-F', f'fileToUpload=@{video_file}', 'https://litterbox.catbox.moe/resources/internals/api.php']


def _download_qr(qr_path, url):
    providers = [
        'https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={}',
        'https://quickchart.io/qr?size=300&text={}',
        'https://chart.googleapis.com/chart?cht=qr&chs=300x300&chl={}'
    ]
    enc = urllib.parse.quote(url, safe='')
    for p in providers:
        api = p.format(enc)
        rc, _, _, cancelled = _run_curl_cancellable(['-L','-o',qr_path, api], ADDON_NAME, 'جارٍ توليد QR ...', max_time=10)
        if cancelled:
            return False
        if rc==0 and os.path.exists(qr_path) and os.path.getsize(qr_path)>0:
            return True
    return False


def share_video(video_file):
    enable_share = _get_bool(ADDON.getSetting('enable_share'), True)
    if not enable_share:
        xbmcgui.Dialog().ok(ADDON_NAME,'المشاركة معطّلة من الإعدادات.')
        return
    try:
        idx = int(ADDON.getSetting('upload_backend') or '0')
    except Exception:
        idx = 0
    backend = 'litterbox' if idx == 0 else 'catbox'

    args = _build_upload_cmd(video_file, backend)
    label = 'Litterbox (72h)' if backend=='litterbox' else 'Catbox'
    rc, out, err, cancelled = _run_curl_cancellable(args, ADDON_NAME, f'جارٍ الرفع عبر {label} ...', max_time=45)

    if cancelled:
        xbmcgui.Dialog().notification(ADDON_NAME,'تم إلغاء الرفع', xbmcgui.NOTIFICATION_INFO, 1200)
        return

    if rc == 0 and out.startswith('http'):
        url = out
        qr_path = os.path.join(ADDON_PROFILE, 'qr.png')
        if _download_qr(qr_path, url):
            class QRDialog(xbmcgui.WindowDialog):
                def __init__(self, u, q):
                    super().__init__()
                    w=xbmcgui.getScreenWidth(); h=xbmcgui.getScreenHeight()
                    bg = xbmcgui.ControlImage(0,0,w,h,'',colorDiffuse='0x80000080')
                    self.addControl(bg)
                    img = xbmcgui.ControlImage((w-225)//2,(h-225)//2,225,225,q)
                    self.addControl(img)
                    btn = xbmcgui.ControlButton((w-200)//2, min(h-80,(h+225)//2+16),200,50,'إغلاق')
                    self.addControl(btn)
                    self.setFocus(btn)
                def onAction(self, a):
                    if a in (xbmcgui.ACTION_PREVIOUS_MENU, xbmcgui.ACTION_NAV_BACK): self.close()
                def onControl(self, c):
                    try:
                        if c.getLabel()=='إغلاق': self.close()
                    except Exception: pass
            d=QRDialog(url, qr_path); d.doModal(); del d
            try: os.remove(qr_path)
            except Exception: pass
        else:
            xbmcgui.Dialog().textviewer('رابط المشاركة', url)
        return

    xbmcgui.Dialog().ok(ADDON_NAME, 'فشل في رفع الفيديو.\n' + f'rc={rc}\nout[:120]={out[:120]}\nerr[:120]={err[:120]}')

# ---------- Logs & tools ----------
def _zip_logs():
    """Create a zip with logs and small context for support."""
    ts=time.strftime('%Y%m%d_%H%M%S')
    outzip=os.path.join(ADDON_PROFILE, f'logs_{ts}.zip')
    with zipfile.ZipFile(outzip, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in (ERROR_LOG, FFMPEG_LOG, STATUS_FILE, PID_FILE):
            if p and os.path.exists(p):
                z.write(p, arcname=os.path.basename(p))
        # include settings.xml from resources so user can share config schema
        settings_res=os.path.join(ADDON_PATH,'resources','settings.xml')
        if os.path.exists(settings_res):
            z.write(settings_res, arcname='settings_schema.xml')
    return outzip


def _open_logs_folder():
    xbmc.executebuiltin(f'ActivateWindow(videos,{ADDON_PROFILE},return)')

# ---------- Toggle with dialog ----------
def toggle_with_dialog():
    is_rec, rec_file = load_status()
    if is_rec:
        recorded = stop_recording()
        if recorded and os.path.exists(recorded):
            enable_share = _get_bool(ADDON.getSetting('enable_share'), True)
            options = ['مشاركة','تشغيل','إغلاق'] if enable_share else ['تشغيل','إغلاق']
            sel = xbmcgui.Dialog().select('خيارات التسجيل', options)
            if enable_share and sel == 0:
                share_video(recorded)
                return 'shared'
            elif (enable_share and sel == 1) or (not enable_share and sel == 0):
                play_file(recorded)
                return 'played'
            else:
                return 'stopped'
        return 'stopped'
    else:
        ok = start_recording()
        return 'started' if ok else 'error'

# ---------- Entry ----------
def main():
    handle = int(sys.argv[1]) if len(sys.argv)>1 else -1
    succeeded = True
    state = None
    try:
        if len(sys.argv)>2 and sys.argv[2].startswith('?'):
            params = dict(urllib.parse.parse_qsl(sys.argv[2][1:]))
            action = (params.get('action') or '').lower()
            if action == 'about':
                xbmcgui.Dialog().ok(ADDON_NAME, 'المطوّر: Meshal A. Alsaedi\nTelegram: @i_Meshal')
                state = 'about'
            elif action == 'export_logs':
                p=_zip_logs()
                xbmcgui.Dialog().ok(ADDON_NAME, f'تم إنشاء الحزمة:\n{p}')
                state='export_logs'
            elif action == 'open_logs':
                _open_logs_folder(); state='open_logs'
            else:
                state = toggle_with_dialog()
                succeeded = (state!='error')
        else:
            state = toggle_with_dialog()
            succeeded = (state!='error')
    finally:
        try:
            import xbmcplugin
            xbmcplugin.endOfDirectory(handle, succeeded=succeeded)
        except Exception:
            pass
        if state == 'started':
            xbmc.executebuiltin('Action(Back)')

if __name__ == '__main__':
    main()

# Author: Meshal (Telegram: @i_Meshal)
# حفظ الحقوق: @i_Meshal
# Screen Rec. Plugin
# Developed by Meshal © 2025
# Licensed under MIT
# https://github.com/i-Meshal/Screen-Rec./