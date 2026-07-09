#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
config_web.py  -  Web admin panel for the Cube J1 MQTT bridge
Python 2.7 stdlib only: BaseHTTPServer, json, os, re, base64

Serves a small web page on http://<cube-ip>:8080/ that lets you edit the
Wi-Fi and smart-meter / MQTT settings from a browser after installation,
without pulling the USB memory out and re-editing files by hand.

Runs as a separate init service (config_web.rc) so that if it crashes the
measurement bridge keeps running, and it never touches the serial port.
"""

from __future__ import print_function

import os
import re
import json
import base64
import BaseHTTPServer

CONFIG_PATH = "/data/local/config.json"
WPA_PATH    = "/data/misc/wifi/wpa_supplicant.conf"
LOG_PATH    = "/data/local/mqtt_bridge.log"
WPA_SOCKETS = "/data/misc/wifi/sockets"
LISTEN_PORT = 8080

# config.json keys shown on the page, with (label, is_secret, help)
CONFIG_FIELDS = [
    ("br_id",         u"Bルート認証ID",        False, u"電力会社発行の32文字ID"),
    ("br_pwd",        u"Bルートパスワード",     True,  u"電力会社発行の12文字パスワード"),
    ("mqtt_host",     u"MQTTホスト (HAのIP)",   False, u"Home Assistant のIPアドレス"),
    ("mqtt_port",     u"MQTTポート",            False, u"通常は 1883"),
    ("mqtt_user",     u"MQTTユーザー名",        False, u"未設定なら空欄"),
    ("mqtt_pass",     u"MQTTパスワード",        True,  u"未設定なら空欄"),
    ("device_id",     u"デバイスID",            False, u"HA上の識別子 (例: cubej1)"),
    ("serial_port",   u"シリアルポート",        False, u"通常は /dev/ttyS1 (変更不要)"),
    ("poll_interval", u"ポーリング間隔(秒)",    False, u"取得間隔。通常は 60"),
]

INT_KEYS = ("mqtt_port", "poll_interval")


# ---------------------------------------------------------------------------
# Config / Wi-Fi file helpers
# ---------------------------------------------------------------------------

def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)
        f.write("\n")
    os.rename(tmp, CONFIG_PATH)


def read_wifi():
    """Return (ssid, psk). psk is empty if the stored file uses a hashed key."""
    ssid, psk = "", ""
    try:
        with open(WPA_PATH) as f:
            text = f.read()
        m = re.search(r'ssid="([^"]*)"', text)
        if m:
            ssid = m.group(1)
        m = re.search(r'psk="([^"]*)"', text)
        if m:
            psk = m.group(1)
    except Exception:
        pass
    return ssid, psk


def write_wifi(ssid, psk):
    """Write a clean wpa_supplicant.conf and ask wpa_supplicant to reload it."""
    ssid = ssid.replace('"', '')
    psk = psk.replace('"', '')
    if psk:
        net = ('network={\n'
               '        ssid="%s"\n'
               '        psk="%s"\n'
               '        key_mgmt=WPA-PSK\n'
               '}\n') % (ssid, psk)
    else:
        # open network (no password)
        net = ('network={\n'
               '        ssid="%s"\n'
               '        key_mgmt=NONE\n'
               '}\n') % (ssid,)
    text = ("ctrl_interface=%s\n"
            "update_config=1\n\n%s") % (WPA_SOCKETS, net)
    with open(WPA_PATH, "w") as f:
        f.write(text)
    os.system("chmod 660 %s" % WPA_PATH)
    os.system("chown system:wifi %s" % WPA_PATH)
    os.system("wpa_cli -p %s -i wlan0 reconfigure" % WPA_SOCKETS)


def restart_bridge():
    os.system("stop mqtt_ha_bridge")
    os.system("sleep 1")
    os.system("start mqtt_ha_bridge")


def tail_log(n=100):
    try:
        with open(LOG_PATH) as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except Exception:
        return u"(ログはまだありません)"


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def esc(s):
    s = u"%s" % s
    return (s.replace(u"&", u"&amp;").replace(u"<", u"&lt;")
             .replace(u">", u"&gt;").replace(u'"', u"&quot;"))


PAGE = u"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cube J1 設定</title>
<style>
 body{font-family:sans-serif;max-width:640px;margin:0 auto;padding:16px;
      background:#f5f5f7;color:#222;}
 h1{font-size:20px;} h2{font-size:16px;margin-top:24px;}
 .card{background:#fff;border-radius:10px;padding:16px;margin:12px 0;
       box-shadow:0 1px 3px rgba(0,0,0,.1);}
 label{display:block;font-size:13px;color:#555;margin-top:10px;}
 input{width:100%;box-sizing:border-box;padding:8px;font-size:15px;
       border:1px solid #ccc;border-radius:6px;margin-top:3px;}
 .hint{font-size:11px;color:#999;margin-top:2px;}
 button{background:#0a84ff;color:#fff;border:0;border-radius:6px;
        padding:10px 18px;font-size:15px;margin-top:14px;cursor:pointer;}
 button.gray{background:#888;}
 .msg{background:#e5f6e5;border:1px solid #66c266;color:#227722;
      padding:10px;border-radius:6px;margin:10px 0;}
 .warn{background:#fff4e5;border:1px solid #ffc266;color:#996600;
       padding:10px;border-radius:6px;margin:10px 0;font-size:13px;}
 pre{background:#111;color:#0f0;padding:10px;border-radius:6px;
     overflow:auto;font-size:11px;max-height:320px;}
</style></head><body>
<h1>Cube J1 MQTT 設定</h1>
{{MSG}}
<div class="card">
 <h2>Wi-Fi 設定</h2>
 <form method="post" action="/save_wifi">
  <label>SSID<input name="ssid" value="{{SSID}}"></label>
  <label>パスワード<input name="psk" value="{{PSK}}"
         placeholder="空欄なら変更せず / オープンなら空"></label>
  <div class="warn">Wi-Fiを変更すると一時的にこの画面へ接続できなくなる場合があります。</div>
  <button type="submit">Wi-Fiを保存して反映</button>
 </form>
</div>
<div class="card">
 <h2>スマートメーター / MQTT 設定</h2>
 <form method="post" action="/save_config">
  {{FIELDS}}
  <button type="submit">保存してブリッジを再起動</button>
 </form>
</div>
<div class="card">
 <h2>動作ログ (最新100行)</h2>
 <form method="get" action="/"><button class="gray" type="submit">再読み込み</button></form>
 <pre>{{LOG}}</pre>
</div>
<div class="card">
 <h2>メンテナンス</h2>
 <form method="post" action="/reboot"
       onsubmit="return confirm('本体を再起動します。よろしいですか?');">
  <button class="gray" type="submit">本体を再起動</button>
 </form>
</div>
</body></html>"""


def render(msg_html=""):
    cfg = load_config()
    ssid, psk = read_wifi()
    rows = []
    for key, label, secret, help_text in CONFIG_FIELDS:
        val = cfg.get(key, "")
        rows.append(
            u'<label>%s<input name="%s" value="%s"></label>'
            u'<div class="hint">%s</div>'
            % (esc(label), esc(key), esc(val), esc(help_text)))
    html = (PAGE
            .replace("{{MSG}}", msg_html)
            .replace("{{SSID}}", esc(ssid))
            .replace("{{PSK}}", esc(psk))
            .replace("{{FIELDS}}", u"".join(rows))
            .replace("{{LOG}}", esc(tail_log())))
    return html.encode("utf-8")


def msg_box(text, warn=False):
    cls = "warn" if warn else "msg"
    return u'<div class="%s">%s</div>' % (cls, esc(text))


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPServer.BaseHTTPRequestHandler):
    server_version = "CubeJ1Config/1.0"

    def _auth_ok(self):
        cfg = load_config()
        pw = cfg.get("web_password", "")
        if not pw:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                raw = base64.b64decode(header[6:]).decode("utf-8")
                _, _, given = raw.partition(":")
                if given == pw:
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Cube J1"')
        self.end_headers()
        return False

    def _send_html(self, body, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_post(self):
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length) if length else b""
        try:
            import urlparse
            return urlparse.parse_qs(data.decode("utf-8"), keep_blank_values=True)
        except Exception:
            return {}

    def log_message(self, fmt, *args):
        pass  # stay quiet

    def do_GET(self):
        if not self._auth_ok():
            return
        if self.path.split("?")[0] not in ("/", "/index.html"):
            self._send_html(b"Not found", 404)
            return
        self._send_html(render())

    def do_POST(self):
        if not self._auth_ok():
            return
        form = self._read_post()

        def g(name):
            v = form.get(name, [""])
            return v[0] if v else ""

        path = self.path.split("?")[0]
        try:
            if path == "/save_wifi":
                write_wifi(g("ssid"), g("psk"))
                self._send_html(render(msg_box(u"Wi-Fi設定を保存し、再接続を要求しました。")))
                return
            if path == "/save_config":
                cfg = load_config()
                for key, _label, _secret, _help in CONFIG_FIELDS:
                    val = g(key)
                    if key in INT_KEYS:
                        try:
                            val = int(val)
                        except Exception:
                            continue
                    cfg[key] = val
                save_config(cfg)
                restart_bridge()
                self._send_html(render(
                    msg_box(u"設定を保存し、ブリッジを再起動しました。"
                            u"メーター再接続に約1分かかります。")))
                return
            if path == "/reboot":
                self._send_html(render(msg_box(u"本体を再起動します...", warn=True)))
                os.system("sync")
                os.system("reboot")
                return
        except Exception as e:
            self._send_html(render(msg_box(u"エラー: %s" % e, warn=True)))
            return
        self._send_html(b"Not found", 404)


def main():
    httpd = BaseHTTPServer.HTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
