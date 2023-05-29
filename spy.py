#!/usr/bin/env python3

# pylint: disable=line-too-long, consider-using-f-string

"""
################################################################################
# SPY.PY                                                                 # slv #
################################################################################
# Shows logged in users in terminal or as web page                             #
# Like gl_spy which comes with glftpd and also webspy from foo-tools           #
# Uses SHM and glftpd's 'ONLINE' C struct, module sysv_ipc is required         #
# See README and comments in spy.conf for install and config instructions      #
################################################################################
"""

# TODO:
# - separated pywho / cli(+web) --  remove sitewho code
# - readd normal showusers (?)
# - spymode w/ 'real' events, ncurses etc?
# - cli spy keys: rewrite func cli_input or try threads ?
# - full screen (re)size cli py
# - replace custom args parsing with argparse mod ?
# - cut/pad all str vars to 80 chars, try -- print('\n'.join(_.strip() for _ in re.findall(r'.{1,75}(?:\s+|$)', _m)))
# - start cli() by default? (no args)

import string
import struct
import re
import time
import datetime
import configparser
import os
import sys
import socket
import calendar
import collections
import signal
import select
# from tokenize import group
import tty
# from typing import final

import subprocess

import sysv_ipc

# global vars used like #ifdef's in orig sitewho.c
_WITH_GEOIP = True
_WITH_HTTPD = True
_WITH_FLASK = True

if _WITH_HTTPD:
    import http.server
    import socketserver
    import threading

if _WITH_FLASK:
    import flask

if _WITH_GEOIP:
    # pylint: disable=import-error
    import geoip2.webservice

VERSION = "20230527"

# TODO: (?) test os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.basename(sys.argv[0])
SCRIPTDIR = os.path.dirname(os.path.realpath((sys.argv[0])))
SCRIPTNAME = os.path.splitext(SCRIPT)[0]
TTY_SETTINGS = tty.tcgetattr(sys.stdin)

UPLOADS = DOWNLOADS = 0
TOTAL_UP_SPEED = TOTAL_DN_SPEED = 0
ONLINEUSERS = BROWSERS = IDLERS = 0
SHOWALL = 0
GEOIP2_BUF = {}
GEOIP2_CLIENT = None

HTTPD_MODE = 0
FLASK_MODE = 0

SPY_FULLVER = f"spy-{VERSION}"
if _WITH_GEOIP:
    SPY_FULLVER += '-geoip'
if _WITH_HTTPD:
    SPY_FULLVER += '-httpd'
if _WITH_FLASK:
    SPY_FULLVER += '-flask'

# handle args
##############
if '-h' in sys.argv or '--help' in sys.argv:
    print(f'./{SCRIPTNAME} [--htm|--srv|--web]')
    sys.exit(0)
elif '-v' in sys.argv or '--version' in sys.argv:
    print(SPY_FULLVER)
    sys.exit(0)
elif len(sys.argv) > 1 and len(sys.argv[1]) == 5:
    #if ['--spy', '---cli'] in sys.argv:
    #
    if '--srv' in sys.argv:
        if _WITH_HTTPD:
            HTTPD_MODE = 1
    elif '--web' in sys.argv:
        if _WITH_FLASK:
            FLASK_MODE = 1
        else:
            sys.exit(0)
else:
    if len(sys.argv) > 1 and sys.argv[1][0] == '-':
        print("Error: invalid option, try '-h'\n")
        sys.exit(1)


# config file
##############

CONFIGFILE = f'{SCRIPTDIR}/{SCRIPTNAME}.conf'
config = configparser.ConfigParser()
cfg_errors = []
for cfg_path in set([CONFIGFILE, f'{SCRIPTDIR}/spy.conf']):
    try:
        with open(cfg_path, 'r', encoding='utf-8', errors='ignore') as cfg_file:
            config.read_string("[DEFAULT]\n" + cfg_file.read())
    except IOError as cfg_err:
        cfg_errors.append(cfg_err)
if len(cfg_errors) > 0:
    for cfg_err in cfg_errors:
        print(cfg_err)
    print('Error: opening config file')
    sys.exit(1)

layout = {}
tmpl_str = {}
tmpl_sub = {}

default = {
    'header':       ".-[SPY.PY]--------------------------------------------------------------.",
    'footer':       "`------------------------------------------------------------[SPY.PY]---'",
    'separator':    " -----------------------------------------------------------------------",
}
tls_mode = [
    0, 'None',       # no ssl
    1, 'Control',    # ssl on control
    2, 'Both'        # ssl on control and data
]

try:
    glrootpath = config['DEFAULT']['glrootpath']
    husers = config.get('DEFAULT', 'hiddenusers', fallback='')
    hgroups = config.get('DEFAULT', 'hiddengroups', fallback='')
    mpaths = config.get('DEFAULT', 'maskeddirectories', fallback='')
    ipc_key = config.get('DEFAULT', 'ipc_key', fallback='')
    maxusers = config.getint('DEFAULT', 'maxusers', fallback=20)
    nocase = config.getboolean('DEFAULT', 'case_insensitive', fallback=False)
    count_hidden = config.getboolean('DEFAULT', 'count_hidden', fallback=True)
    idle_barrier = config.getint('DEFAULT', 'idle_barrier', fallback=30)
    threshold = config.getint('DEFAULT', 'speed_threshold', fallback=1024)
    color = config.getint('DEFAULT', 'color', fallback=1)
    debug = config.getint('DEFAULT', 'debug', fallback=0)
    flask_host = config.get('WEB', 'flask_host', fallback='localhost')
    flask_port = config.get('WEB', 'flask_port', fallback=5000)
    geoip2_enable = config.getboolean('GEOIP', 'geoip2_enable', fallback=False)
    geoip2_accountid = config['GEOIP']['geoip2_accountid']
    geoip2_licensekey = config['GEOIP']['geoip2_licensekey']
    geoip2_proxy = config.get('GEOIP', 'geoip2_proxy', fallback=None)
    layout['header'] = config.get('THEME', 'header', fallback=default['header'])
    layout['footer'] = config.get('THEME', 'footer', fallback=default['footer'])
    layout['separator'] = config.get('THEME', 'separator', fallback=default['separator'])
    tmpl_str['upload'] = config['THEME']['template_upload']
    tmpl_str['download'] = config['THEME']['template_download']
    tmpl_str['info'] = config['THEME']['template_info']
    tmpl_str['totals'] = config['THEME']['template_totals']
    tmpl_str['users'] = config['THEME']['template_users']
    tmpl_sub['hrchar'] = config.get('THEME', 'hrchar', fallback=':')
    tmpl_sub['delimiter'] = config.get('THEME', 'delimiter', fallback='|')
    emoji = config.getboolean('THEME', 'emoji', fallback=False)
except (KeyError, configparser.InterpolationError) as conf_err:
    print(f'ERROR: check config file\n{conf_err}')
    sys.exit(1)

CHIDDEN = 1 if count_hidden else 0
MAXUSERS = maxusers if maxusers else 0
THRESHOLD = threshold if threshold else 0
IDLE_BARRIER = idle_barrier if idle_barrier else 0

if _WITH_FLASK:
    FLASK_OPTIONS = {}
    FLASK_OPTIONS['host'] = flask_host
    FLASK_OPTIONS['port'] = flask_port
    # flask dev mode
    if os.getenv("FLASK_DEBUG") == 1:
        FLASK_OPTIONS['debug'] = True


# glftpd data
##############

# shm and struct (default ipc_key: 0x0000dead=57005)
IPC_KEY = ipc_key if ipc_key else "0x0000DEAD"
KEY = int(IPC_KEY, 16)
NULL_CHAR = b'\x00'
if debug > 3:
    print(f'DEBUG:\tIPC_KEY={IPC_KEY} KEY={KEY} sysv_ipc.SHM_RDONLY={sysv_ipc.SHM_RDONLY}\n',
          f'\tfmt = {KEY:#010x}', id(KEY))

# converted from structonline.h and arranged like struct_ONLINE below:
# tag(64s) username(24s) status(h) ... procid(i)
STRUCT_FORMAT = ' \
  64s  24s  256s  h  256s  256s  i  i  \
  2i \
  2i \
  2I \
  2I \
  i  \
'

# pylint: disable=invalid-name
struct_ONLINE = collections.namedtuple(
    'struct_ONLINE',
    'tagline username status ssl_flag host currentdir groupid login_time \
    tstart_tv_sec tstart_tv_usec  \
    txfer_tv_sec txfer_tv_usec    \
    bytes_xfer1 bytes_xfer2       \
    bytes_txfer1 bytes_txfer2     \
    procid'
)

# set global var with gl version
p_glbin = subprocess.Popen(
    f'{glrootpath}/bin/glftpd', stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
)
glbin_out, glbin_err = p_glbin.communicate()
p_glbin.stdin.close()
if not glbin_err:
    GL_VER = glbin_out.decode().split('\n', maxsplit=1)[0]
else:
    GL_VER = ""

# set global var with groupfile contents (/etc/group)
try:
    with open(f'{glrootpath}/etc/group', 'r', encoding='utf-8', errors='ignore') as grp_file:
        GROUPFILE = grp_file.readlines()
except IOError:
    if debug > 0:
        print(f'DEBUG: IOError (glrootpath={glrootpath})')
    if os.getenv("FLAGS") and os.getenv("RATIO") and os.getenv("TAGLINE"):
        print('DEBUG: IOError env var flags')
        with open('/etc/group', 'r', encoding='utf-8', errors='ignore') as grp_file:
            GROUPFILE = grp_file.readlines()
    else:
        GROUPFILE = None

# set global var for users dir (/ftp-data/users)
USERS_DIR=None
for udir_name in [f"{glrootpath}/ftp-data/users", "/ftp-data/users"]:
    if os.path.isdir(udir_name):
        USERS_DIR=udir_name
        break

# set global var for totalusers, use spy cfg or glconf's maxusers
glconf_max = 0
for glconf_fname in [f'{glrootpath}/../glftpd.conf', f'{glrootpath}/glftpd.conf', '/etc/glftpd.conf']:
    try:
        with open(glconf_fname, 'r', encoding='utf-8', errors='ignore') as glconf:
            for glconf_line in glconf.readlines():
                if re.search(r'^max_users \d', glconf_line):
                    for mu_cnt in glconf_line.split()[1:]:
                        glconf_max += int(mu_cnt)
                    break
    except IOError:
        pass
if MAXUSERS == -1 and glconf_max > 0:
    TOTALUSERS = glconf_max
else:
    TOTALUSERS = maxusers


# geoip
########

if _WITH_GEOIP and geoip2_enable:
    GEOIP2_CLIENT = geoip2.webservice.Client(
        geoip2_accountid, geoip2_licensekey, host='geolite.info',
        proxy=geoip2_proxy if geoip2_proxy not in [None, 'None'] else None
    )


# theme
########

layout_keys   = ['header', 'footer', 'separator']
tmpl_str_keys = ['upload', 'download', 'info', 'totals', 'users']

# use unicode for layout and template keys to make sure we output ansi escapes
for t_key in layout_keys:
    layout[t_key] = layout[t_key].encode().decode('unicode-escape')
for t_key in tmpl_str_keys:
    tmpl_str[t_key] = tmpl_str[t_key].encode().decode('unicode-escape')

# strip colors from output if running from gl and '5' is not in FLAGS, or color=0
if color == 0:
    re_esc = re.compile(r'(?:\x1B[@-_]|[\x80-\x9F])[0-?]*[ -/]*[@-~]')
    for t_key in layout_keys:
        layout[t_key] = re_esc.sub('', layout[t_key])
    for t_key in tmpl_str_keys:
        tmpl_str[t_key] = re_esc.sub('', tmpl_str[t_key])


# classes
###########

class User:
    """ user, with struct as namedtuple """
    uploads = UPLOADS
    downloads = DOWNLOADS
    total = 0
    total_up_speed = TOTAL_UP_SPEED
    total_dn_speed = TOTAL_DN_SPEED
    total_speed = 0
    unit = "KB/s"
    browsers = BROWSERS
    idlers = IDLERS
    onlineusers = ONLINEUSERS
    geoip2_client = GEOIP2_CLIENT if GEOIP2_CLIENT else None
    geoip2_shown_err = False

    def __init__(self, user_tuple, online=0, addr="", ip="0.0.0.0", mb_xfered=0):
        self.user_tuple = user_tuple
        self.name = self.get_name()
        self.group = self.get_gid()
        self.online = online
        self.addr = addr
        self.ip = ip
        self.mb_xfered = mb_xfered
        User.onlineusers += 1

    def get_name(self) -> str:
        """ get username from tuple """
        return self.user_tuple.username.split(NULL_CHAR, 1)[0].decode()

    def get_gid(self) -> str:
        """ get gid from tuple as group name """
        if self.user_tuple.groupid >= 0:
            return get_group(self.user_tuple.groupid) if get_group(self.user_tuple.groupid) else ""
        return ""

    def get (self, attr):
        """ get attributes from tuple """
        r = getattr(self.user_tuple, attr)
        # print('DEBUG: type r=', type(r))
        if isinstance(r, bytes):
            return r.split(NULL_CHAR, 1)[0].decode()
        return r

    def bytes_xfer(self) -> int:
        """ bytes_xfer: 2 uint32 to uint64 """
        return self.get('bytes_xfer2') * pow(2, 32) + self.get('bytes_xfer1')

    def bytes_txfer(self) -> int:
        """ bytes_txfer: 2 uint32 to uint64 """
        return self.get('bytes_txfer2') * pow(2, 32) + self.get('bytes_txfer1')


class Handler(http.server.BaseHTTPRequestHandler):
    """ HTTP Requests """
    def do_GET(self):
        """ GET Method """
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(bytes("<!DOCTYPE html><html lang='en'>\n", "utf-8"))
        self.wfile.write(bytes("<head>\n<title>webspy (http.server)</title>\n</head>\n", "utf-8"))
        self.wfile.write(bytes("<body>\n", "utf-8"))
        self.wfile.write(bytes(fmt_html(), "utf-8"))
        self.wfile.write(bytes("\n</body>\n</html>\n", "utf-8"))


class TCPServerThread(threading.Thread):
    """ Thread for http server """
    def run(self):
        """ Start server """
        with socketserver.TCPServer(("", 8080), Handler, False) as httpd:
            httpd.allow_reuse_address = True
            httpd.server_bind()
            httpd.server_activate()
            httpd.serve_forever()


class Cursor:
    """ return cursor control code """
    def __new__(cls, control, num=0):
        return {
            'H':    '\N{ESC}[H',            # move cursor to home position (0, 0)
            'A':    f'\N{ESC}[{num}A',      # move cursor up <num> lines
            'C':    f'\N{ESC}[{num}C',      # move cursor right <num> columns
            'E':    f'\N{ESC}[{num}E',      # start of next line, <num> lines down
            'F':    f'\N{ESC}[{num}F',      # start of prev line, <num> lines down
            '0J':   '\N{ESC}[0J',           # erase from cursor until end of screen
            '2J':   '\N{ESC}[2J',           # erase entire screen
        }[control]


class Style:
    """ return escape code sequence or empty string if color is off """
    def __new__(cls, mode):
        if color == 0:
            return ''
        return  {
            'r':    '\x1b[0m',      # reset all
            'b':    '\x1b[1m',      # bold
            'u':    '\x1b[4m',      # underline
            'bl':   '\x1b[5m',      # blink
            'rb':   '\x1b[22m',     # reset bold
        }[mode]


class Color:
    """ return color code or empty string if color is off """   
    map = {
        'k':    0,      # black
        'r':    1,      # red
        'g':    2,      # green
        'y':    3,      # yellow
        'b':    4,      # blue
        'm':    5,      # magenta
        'c':    6,      # cyan
        'w':    7,      # white
        'd':    9       # default
    }
    def __new__(cls, fg, bg):
        if color == 0:
            return ''
        return f'\x1b[1;{Color.map[fg]+30};{Color.map[bg]+40}m'


# functions
############

def get_group(gid) -> string:
    """ get group name using gid """
    if GROUPFILE:
        for line in GROUPFILE:
            if line.split(':')[2] == str(gid):
                return line.split(':')[0]
    return None


def get_gid(g_name) -> int:
    """ get gid using group name """
    if GROUPFILE:
        for line in GROUPFILE:
            if line.split(':')[0] == g_name:
                return line.split(':')[2]
    return 0


def get_idle(seconds) -> str:
    """ calc idle time """
    return time.strftime("%H:%M:%S", time.gmtime(seconds))


def get_filesize(filename) -> int:
    """ get filesize in bytes """
    for file in filename, f'{glrootpath}{filename}':
        try:
            return os.path.getsize(file)
        except OSError:
            pass
    return 0


def get_geocode(client, userip, shown_err) -> list:
    """ get geoip2 country code for ip """
    iso_code = "xX"
    if debug > 0:
        for _ in ['127.', '10.', '172.16.1', '172.16.2', '172.16.3', '192.168.']:
            if userip.startswith(_):
                if debug > 3:
                    print(f'DEBUG: geoip2 MATCH {_} in {userip}')
                return [client, 'DEBUG', shown_err]
    if GEOIP2_BUF.get(userip):
        iso_code = GEOIP2_BUF[userip]
    else:
        try:
            if debug == 1:
                print('DEBUG: got cached GEOIP2_BUF[userip]', GEOIP2_BUF[userip])
            iso_code = client.country(userip).country.iso_code
            GEOIP2_BUF[userip] = iso_code
        except geoip2.errors.GeoIP2Error as g_err:
            # var shown_err makes sure we only show the error once
            if (g_err.__class__.__name__ in ['AddressNotFoundError', 'reqOutOfQueriesError']) and shown_err == 0:
                shown_err = 1
                print("\n{0:<80}\n".format(f'Error: geoip2 {g_err.__class__.__name__} ({g_err})'))
                time.sleep(2.5)
                print(f"{Cursor('F',3)}{Cursor('0J')}{Cursor('F',1)}")
    return [client, iso_code, shown_err]


def conv_speed(speed) -> list:
    """ convert and format speed """
    if speed > (THRESHOLD * THRESHOLD):
        return [float('{:7.2f}'.format(speed / 1024**2)), 'GB/s']
    elif speed > THRESHOLD:
        return [float('{:7.1f}'.format(speed / 1024)), 'MB/s']
    else:
        return [float('{:7.0f}'.format(speed)), 'KB/s']


def cli_max_col(message) -> str:
    """ format string msg with max columns """
    return "{0:<{1}.{1}}".format(message, os.get_terminal_size().columns)


def cli_stty_sane():
    """ default terminal settings, turn on echo """
    # run 'stty sane' cmd
    #os.system("stty sane")
    # use ascii esc codes
    #print(f"{Color('k','k')}{Style('r')}")
    #print('\N{ESC}C')
    #print('\N{DC1}')
    # restore orig tty settings
    tty.tcsetattr(sys.stdin, tty.TCSANOW, TTY_SETTINGS)


# TODO: add popup prompts? e.g. 'k':  [ Kill user: ____ ]
#       add more cursor keys, e.g. space and/or enter for userinfo
#       cleanup cli_input

def cli_input(user_action):
    """ read user input from stdin """
    #tty.setcbreak(sys.stdin.fileno())
    tty.setraw(sys.stdin.fileno())
    #user_action = 0
    screen_redraw = 0
    key = ""
    if select.select([sys.stdin], [], [], 0.5) == ([sys.stdin], [], []):
        key = sys.stdin.buffer.raw.read(3).decode(sys.stdin.encoding)
        #print(f"{Cursor('2J')}{Cursor('H')}")
        if key[:2].strip().isdigit():
            user_action = 1
            screen_redraw = 0
        elif key in ['v', 'V']:
            key = 0
            user_action = 1
            screen_redraw = 0
        elif key in ['k', 'K']:
            user_action = 2
            screen_redraw = 1
        elif key in ['h', 'H', '?']:
            user_action = 3
            screen_redraw = 1
        elif key in ['n', 'N', '\N{ESC}[C']:
            user_action = 4
            screen_redraw =  0
        elif key in ['p', 'P', '\N{ESC}[D']:
            user_action = 5
            screen_redraw =  0
        elif key in ['q', 'Q']:
            user_action = 9
            screen_redraw =  0
        elif key == '\N{ESC}':
            if user_action == 0:
                user_action = 9
                screen_redraw = 0
            elif user_action == 1:
                user_action = 6
                screen_redraw = 0
        elif key == '\x03':
            cli_break(any, any)
    tty.tcsetattr(sys.stdin, tty.TCSANOW, TTY_SETTINGS)
    return dict(key=key, user_action=user_action, screen_redraw=screen_redraw)


def cli_help_text():
    """ show help box """
    upos = 20
    lpos = 4
    rpad = 10
    width = 80 - lpos - rpad
    help_text = [
        "",
        f"{' '*30}Help",
        "",
        "  Press 'v' to view login info",
        "    Left/right 'n' and 'p' keys for next/previous login",
        "    Use 'k' to kick selected user *needs root*",
        "    ESC returns to main screen",
        "",       
        "  Shortcut keys '0-9' will jump to user login [#NUM]",
        "  Press 'q' key to Quit",
        "",
        f"{'_'*(width)}",
    ]
    print(f"{Cursor('A',upos)}")
    print(f"{Cursor('C',lpos)}{'_'*(width+2)}")
    for line in help_text:
        print(f"{Cursor('C',lpos)}{Color('k','w')}|{line}{' '*(width-len(line))}|")
    print(f"{Style('r')}")


# TODO: - add username as alt input, instead of u_idx
#       - 'reload' users?
def cli_userinfo(users, u_idx) -> list:
    """ show formatted user details """
    print(layout['header'])
    if users[u_idx].get('procid'):
        user_ssl = users[u_idx].get('ssl_flag')
        tls_msg = tls_mode[user_ssl] if user_ssl in range(0, len(tls_mode)) else 'UNKNOWN'
        print(f"  {Style('u')}LOGIN{Style('r')} [#{u_idx}]:")
        print(f"    Username: '{Style('b')}{users[u_idx].name}{Style('r')}'")
        print(f"    PID: {users[u_idx].get('procid')}  SSL: {tls_msg}")
        print(f"    RHost: {users[u_idx].get('host')}")
        print(f"    Tagline: {users[u_idx].get('tagline')}")
        print(f"    Currentdir: {users[u_idx].get('currentdir')}")
        print(f"    Status: {users[u_idx].get('status')}")
        print(f"    Last DL: {round(int(users[u_idx].bytes_txfer()) / 1024**3, 1)}GB")
        if color == 0:
            print(default['separator'])
        else:
            print("{maincolor}{separator}{r}".format(
                maincolor=config.get('THEME', 'maincolor'), separator=default['separator'], r=Style('r')
            ).encode().decode('unicode-escape'))
        r = get_userfile(users[u_idx].name)
        if r.get('status') == "Success":
            print(f"  {Style('u')}Userfile{Style('r')}:")
            for k, v in r.get('result').items():
                out = f"{' ':>4.4}{k}: {v}"
                width = (80 - len(out)) if len(out) < 80 else 72
                print('{0:<{1}.{1}}'.format(out, width))
        else:
            print("{:<80}".format(f" User '{users[u_idx].name}' not found..."))
            time.sleep(2)
        print(layout['footer'])


# TODO: - refactor func?
#       - fix wrong key response 'User not found or invalid option ...' (first time)
#       - slow reponse to key (sleep)
#       - help 'overlay': calc padding vars instead of statics (term cols)
#       - fix tmp var: users-> users
#       - move prompt to sep. def ?

def cli_action(users, user_action, screen_redraw, key):
    """ get user input and run action """
    # action: userinfo
    if user_action == 1:
        u_idx = 0
        # shortcut keys 0-9
        if isinstance(key, str) and key.isdigit() and int(key) in range(0, len(users)):
            u_idx = int(key)
        print(f"{Cursor('2J')}{Cursor('H')}")
        # show user details and wait for user input
        cli_userinfo(users, u_idx)
        i = 0
        input_result = kill_result = None
        while True:
            input_result = cli_input(user_action)
            # kill
            if input_result.get('user_action') == 2:
                if u_idx in range(0, len(users)):
                    u_name = users[int(u_idx)].get('username')
                    kill_result = kill_procid(u_name, users)
                    if kill_result.get('status') == "Success":
                        print("{:<80}".format(f"{kill_result.get('status')} Killed PID '{kill_result.get('procid')} ..."))
                        time.sleep(2)
                    else:
                        print("{:<80}".format(f"{kill_result.get('error')}"))
                        time.sleep(3)
                    print("{ :<80}")
                print(f"{Cursor('2J')}{Cursor('H')}")
                break
            # next
            if input_result.get('user_action') == 4:
                u_idx = u_idx + 1 if (u_idx + 1 < len(users)) else 0
                print(f"{Cursor('2J')}{Cursor('H')}")
                cli_userinfo(users, u_idx)
            # prev
            elif input_result.get('user_action') == 5:
                u_idx = u_idx-1 if (u_idx-1 < len(users) and u_idx > 0) else 0
                print(f"{Cursor('2J')}{Cursor('H')}")
                cli_userinfo(users, u_idx)
            # back (ESC)
            if input_result.get('user_action') == 6:
                break
            # quit
            if input_result.get('user_action') == 9:
                break
            r_pad = ' ' * (3-i)
            i = 0 if i > 3 else i
            progress = '{0:{fill}<{width}}'.format('', fill='.', width=int(i))
            prompt = (
                f"  > Press {Color('k','w')}n{Style('r')} to view next login [#{u_idx}], " \
                f"{Color('k','w')}p{Style('r')} for previous " \
                f"or {Color('k','w')}ESC{Style('r')} to go back"
            )
            print("")
            print(f"{prompt}{progress}{r_pad}", end="")
            print(f"{Cursor('A', 2)}")
            i += 1
        tty.tcsetattr(sys.stdin, tty.TCSANOW, TTY_SETTINGS)
        user_action = input_result.get('user_action')
        screen_redraw = 1
    # action: show help popup
    elif user_action ==  3:
        cli_help_text()
        while not cli_input(user_action).get('key'):
            time.sleep(0.1)
        user_action = 0
        screen_redraw = 1
    # action: quit (ESC)
    elif user_action == 9:
        sys.exit(0)
    # handle any other key presses
    elif user_action == 0 and len(key) > 0:
        print(f"{' ':<80}")
        print(f'{"":>2.2}{"User not found or invalid option ...":<78}')
        print(f"{' ':<80}")
        #print(f"{Cursor('F',4)}")
        time.sleep(1)
    else:
        user_action = 0
        screen_redraw = 1
    return [user_action, screen_redraw]


def get_userfile(u_name) -> dict:
    """ get useful fields from userfile """
    userfile_text = ""
    try:
        with open(f'{USERS_DIR}/{u_name}', 'r', encoding='utf-8', errors='ignore') as userfile:
            userfile_text = userfile.readlines()
    except FileNotFoundError:
        return dict(status="FileNotFound")
    if userfile_text == "":
        return dict(status="UserNotFound")
    u_fields = {}
    value = []
    prev = ""
    for line in userfile_text:
        for key in ['FLAGS', 'CREDITS', 'IP']:
            if key in line:
                if key != prev:
                    value = []
                if line.startswith('CREDITS'):
                    c = re.sub(r'^CREDITS ([^0]\d+).*', r'\1', line)
                    value = f"{round(int(c) / 1024**2)}GB"
                else:
                    value.append(line.strip().split(' ')[1])
                u_fields[key] = value
                prev = key
    return dict(status="Success", result=u_fields)


def kill_procid(u_name, users):
    """ kill user procid """
    for user in users:
        if user.name == u_name:
            if os.popen(f"ps --no-headers -o comm -p {user.get('procid')}").read().strip() == 'glftpd':
                try:
                    procid = user.get('procid')
                    os.kill(int(procid), 15)
                    return dict(status="Success", procid=procid)
                except OSError as err:
                    return dict(status="NotRoot", procid=procid, error=err)
    return dict(status="NotFound")


# TODO:
#   - REMOVED def showusers() -- readd/merge with user_stats?
#   - check replacement def cli()
#   - check fixed 'speed' (now in user obj)
#   - check traf_dir / user_obj -- change flask users template?

def user_stats(user):
    """ calc and format user stats """
    tstop_tv_sec = calendar.timegm(time.gmtime())
    tstop_tv_usec = datetime.datetime.now().microsecond
    traf_dir = None
    mask = noshow = 0
    maskchar = " "

    if user.get('host') != '':
        (_, user.addr) = user.get('host').split('@', 2)[0:2]
        # ipv4/6
        if (''.join((user.addr).split('.', 3)).isdigit()) or (':' in user.addr):
            user.ip = user.addr
        # addr is not a fqdn
        elif '.' not in user.addr:
            user.ip = '127.0.0.1' if user.addr == 'localhost' else '0.0.0.0'
        else:
            try:
                user.ip = socket.gethostbyname(user.addr)
            except OSError:
                pass

    if len(user.get('status')) > 4 and not user.get('status')[4:].startswith('-'):
        user.filename = user.get('status')[4:]
    #else:
    #    user.filename = ''

    # check if user in hidden users/groups
    if ((nocase and ((user.name.lower() in husers.lower()) or (user.group.lower() in hgroups.lower()))) or
            ((user.name in husers) or (user.group in hgroups))):
        if SHOWALL:
            maskchar = '*'
        else:
            noshow += 1

    if noshow == 0 and mpaths:
        if ((maskchar == '') and (user.get('currentdir') in mpaths.split(' ') or (f"{user.get('currentdir')}/" in mpaths.split(' ')))):
            if SHOWALL:
                maskchar = '*'
            else:
                mask += 1

    if _WITH_GEOIP and geoip2_enable:
        (User.geoip2_client, user.iso_code, User.geoip2_shown_err) = get_geocode(User.geoip2_client, user.ip, User.geoip2_shown_err)
        user.ip = f'{user.ip} {user.iso_code}' if (user.ip and user.iso_code) else user.ip

    # user: ul speed
    if (user.get('status')[:4] == 'STOR' or user.get('status')[:4] == 'APPE') and user.bytes_xfer():
        user.mb_xfered = (abs(user.bytes_xfer() / 1024 / 1024)) if user.bytes_xfer() else 0
        traf_dir = "Up"
        user.speed = abs(
            user.bytes_xfer() / 1024 / ((tstop_tv_sec - user.get('tstart_tv_sec')) +
            (tstop_tv_usec - user.get('tstart_tv_usec')) / 1000000)
        )
        if (not noshow and not mask and maskchar != '*') or CHIDDEN:
            User.total_up_speed += user.speed
            User.uploads += 1
        if not mask:
            user.pct = -1
            user.p_bar = '?->'
    # user: dn speed
    elif user.get('status')[:4] == 'RETR' and user.bytes_xfer():
        traf_dir = "Dn"
        realfile = user.get('currentdir')
        my_filesize = get_filesize(realfile)
        if my_filesize < user.bytes_xfer():
            my_filesize = user.bytes_xfer()
        user.pct = abs(
            user.bytes_xfer() / my_filesize * 100
        )
        i = 15 * user.bytes_xfer() / my_filesize
        i = 15 if i > 15 else i
        user.p_bar = f"{'':x<{int(abs(i))}}"
        user.speed = abs(
           user.bytes_xfer() / 1024 / ((tstop_tv_sec - user.get('tstart_tv_sec')) +
                (tstop_tv_usec - user.get('tstart_tv_usec')) / 1000000)
        )
        if (not noshow and not mask and maskchar != '*') or CHIDDEN:
            User.total_dn_speed += user.speed
            User.downloads += 1
    # user: idle time
    else:
        user.p_bar = user.filename = ""
        user.pct = 0
        seconds = tstop_tv_sec - user.get('tstart_tv_sec')
        if ((not noshow and not mask and maskchar != '*') and CHIDDEN):
            if seconds > IDLE_BARRIER:
                User.idlers += 1
            else:
                User.browsers += 1
        user.status = 'Idle: {:>8.8}'.format(get_idle(seconds))

    user.online = get_idle(tstop_tv_sec - user.get('login_time'))

    # user: format both Up/Dn speed to KB/s MB/s GB/s
    if user.bytes_xfer() and traf_dir in ["Up", "Dn"]:
        if not mask:
            user.status = '{}:{:2.2s}{}{}'.format(traf_dir, ' ', *conv_speed(user.speed))


# TODO: - totals -- improve unit, move to class def? or flask template?
#       - cleanup old vars

def get_users() -> list:
    """ create and return user objects containing shm """  
    try:
        memory = sysv_ipc.SharedMemory(KEY, flags=sysv_ipc.SHM_RDONLY, mode=0)
    #except sysv_ipc.ExistentialError as shm_err:
    #    raise Exception(f'Error: {shm_err} (0x{KEY:08X}) No users are logged in?')
    except sysv_ipc.ExistentialError:
        raise RuntimeError('No users found')
    else:
        buf = memory.read()

    # clear objects/attrs
    user = None
    users = []
    User.uploads = UPLOADS
    User.downloads = DOWNLOADS
    User.total = 0
    User.total_up_speed = TOTAL_UP_SPEED
    User.total_dn_speed = TOTAL_DN_SPEED
    User.total_speed = 0
    User.unit = "KB/s"
    User.browsers = BROWSERS
    User.idlers = IDLERS
    User.onlineusers = ONLINEUSERS
    User.geoip2_shown_err = 0

    for user_tuple in struct.iter_unpack(STRUCT_FORMAT, buf):
        if struct_ONLINE._make(user_tuple).procid:
            user = User(struct_ONLINE._make(user_tuple))
            users.append(user)
            user_stats(user)

    User.total = User.uploads + User.downloads
    User.total_speed = User.total_up_speed + User.total_dn_speed

    try:
        memory.detach()
    except (UnboundLocalError, sysv_ipc.Error):
        pass
    if _WITH_GEOIP and geoip2_enable:
        GEOIP2_CLIENT.close()

    return users


# TODO: cleanup -- remove tmp values, remove rep)
def cli():
    """ output users/totals to terminal """
    u_idx = 0

    # init screen drawing related vars
    repeat = 0
    user_action = 0         # 1=cli_userinfo 2=killuser 3=help 4=next 5=prev 6=back 9=quit
    screen_redraw = 0       # 1=redraw logo/header

    print(f"{Cursor('2J')}{Cursor('H')}", end="")
    print(layout['header'])

    while True:
        signal.signal(signal.SIGINT, cli_break)
        try:
            users = get_users()
        except RuntimeError:
            print(f"{Cursor('2J')}{Cursor('H')}", end="")
            print(layout['header'])
            print(f"No users logged in.. Press {Style('b')}CTRL-C{Style('rb')} to quit")
            print(layout['footer'])
            print()
            print(f"{Cursor('F',4)}")
            time.sleep(1)
            continue
        # on redraw -- first clear screen, then show header,
        # move cursor up using ansi escape codes and show user[x] lines
        if repeat > 0 and user_action == 0:
            # print vars for debugging, sleep to be able to actualy view them
            if debug > 4:
                print(f'DEBUG: spy vars user_action={user_action} screen_redraw={screen_redraw}')
                time.sleep(2)
            if screen_redraw == 0:
                # go back up and clear 'l' lines per user + totals + usage lines
                # len(layout['header'].splitlines())
                l = (len(users) * 1) if users else 0
                #print(f"{Cursor('F', l+3+2)}")
                print(f"{Cursor('F', l+5+1)}")
                print(f"{Cursor('0J')}{Cursor('F',2)}", end="")
            else:
                print(f"{Cursor('2J')}{Cursor('H')}", end="")
                print(layout['header'])
                screen_redraw = 0

        # reset user data for every repeat
        u_idx = 0

        # user loop
        for user in users:
            # try to show as much useful info as possible..
            # show pct/progessbar or currentdir on right side
            if user_action == 0:
                if not user.pct and not user.p_bar:
                    pct_spy = ''
                else:
                    #pct_spy = f"{user.pct:>3.0f}%: "
                    pct_spy = f"{user.pct:>.0f}%"
                if user.p_bar:
                    if user.p_bar == '?->':
                        p_bar_spy = f"{user.get('status')[:5]:<22.22}" if (len(user.get('status')) > 5) else f"{' ':<22.22}"
                    else:
                        #p_bar_spy = f'{user.p_bar:<16.16s}'
                        p_bar_spy = f'{user.filename:<.15}'
                else:
                #    # show '-' to indicate(large) file is started
                #    if user.pct > 0:
                #        p_bar_spy = f"{'-':<16.16s} "
                #    else:
                #        p_bar_spy = f"{user.get('currentdir').replace('/site', ''):<22.22}"
                    p_bar_spy = f"{user.get('currentdir').replace('/site', ''):<22.22}"

                # TODO: instead of reusing code below, maybe just show 'filename.rar 13%'

                #if repeat % 8 in range(0, 5):
                #    info_spy = f'{pct_spy}{p_bar_spy}'
                #else:
                #    info_spy = f"{user.get('status'):<.17}"

                info_spy = f'{pct_spy}{p_bar_spy}'

                if user.mb_xfered:
                    print(string.Template(tmpl_str['upload']).substitute(tmpl_sub).format(
                        username=user.name, g_name=user.group, u_idx=u_idx, status=user.status, mb_xfered=user.mb_xfered()
                    ))
                else:
                    print(string.Template(tmpl_str['download']).substitute(tmpl_sub).format(
                        username=user.name, g_name=user.group, u_idx=u_idx, status=user.status, info_spy=info_spy
                    ))
                # TODO: combine/move this to 1 line (info_spy)

                '''
                # right side: switch between showing filename or status
                filename = f'{user.filename:<.15}' if len(user.filename) > 15 else user.filename
                if (user.status[:5] in ['RETR ', 'STOR ']):
                    fn_spy = f'file: {filename}'
                elif (user.status[:5] in ['LIST ', 'STAT ', 'SITE ']) or (user.status == 'Connecting...') or (user.status[5:].startswith('-')):
                    fn_spy = user.status
                else:
                    fn_spy = filename
                # left side: show ip or tagline
                if rep % 8 in range(0, 5):

                    #print(string.Template(tmpl_str['info']).substitute(tmpl_sub).format(info=user.get('tagline'), online=user.online, fn_spy=fn_spy))

                    #print(string.Template(tmpl_str['info']).substitute(tmpl_sub).format(info="{:8.8s} {:>18.18s}".format(
                    #    user.get('tagline'), userip if userip != '0.0.0.0' else 'TODO:addr'), online=user.online, fn_spy=fn_spy
                    #))
                    
                    print(string.Template(tmpl_str['info']).substitute(tmpl_sub).format(
                        info="{:8.8s} {:>18.18s}".format(user.get('tagline'), user.ip if user.ip != '0.0.0.0' else user.addr), online=user.online, fn_spy=fn_spy
                    ))

                else:
                    print(string.Template(tmpl_str['info']).substitute(tmpl_sub).format(info=user.get('tagline'), online=user.online, fn_spy=fn_spy))
                print(layout['separator'].format('', x=x))
                #onlineusers += 1
                '''

            #x += 1
            u_idx += 1

            # TODO: is this the thingy that moves screen up/down if there's too much users to show?
            '''
            hdr_lines = layout['header'].count('\n')
            if ((u_idx * 3) + hdr_lines > os.get_terminal_size().lines and screen_redraw == 0):
                time.sleep(1)
                screen_redraw = 1
            '''

        # showtotals
        if user_action == 0:
            #print(type(users[0].downloads))
            [total_up_speed, unit_up] = conv_speed(users[0].total_up_speed)
            [total_dn_speed, unit_dn] = conv_speed(users[0].total_dn_speed)
            [total_speed, unit_total] = conv_speed(users[0].total_up_speed + users[0].total_dn_speed)
            #print(f'DEBUG: {uploads} {total_up_speed} {total_dn_speed} {total} {total_speed} {unit}'.format(
            #    uploads=users[0].uploads, total_up_speed=total_up_speed, downloads=users[0].downloads, total_dn_speed=total_dn_speed,
            #    total=users[0].uploads + users[0].downloads, total_speed=total_speed, unit_up=unit_up, unit_dn=unit_dn, unit_total=unit_total))
            #sys.exit(0)
            print(string.Template(tmpl_str['totals']).substitute(tmpl_sub).format(
                uploads=users[0].uploads, total_up_speed=total_up_speed, downloads=users[0].downloads, total_dn_speed=total_dn_speed,
                total=users[0].uploads + users[0].downloads, total_speed=total_speed, unit_up=unit_up, unit_dn=unit_dn, unit_total=unit_total
            ))
            print(string.Template(tmpl_str['users']).substitute(tmpl_sub).format(
                space=' ', onlineusers=users[0].onlineusers, maxusers=TOTALUSERS)
            )
            print(layout['footer'])
            print()

        # show usage text and user input prompt
        if user_action == 0:
            print(f"> View users with {Color('k','w')}v{Style('r')} or press {Color('k','w')}h{Style('r')} key for help. Press {Style('b')}CTRL-C{Style('rb')} to quit", end="")
            print(f"{Cursor('A',1)}")
        # handle keyboard input
        input_result = cli_input(user_action)
        key, user_action, screen_redraw = input_result.get('key'), input_result.get('user_action'), input_result.get('screen_redraw')
        [user_action, screen_redraw] = cli_action(users, user_action, screen_redraw, key)

        if _WITH_GEOIP and geoip2_enable:
            time.sleep(2)

        repeat += 1


def cli_break(signal_received, frame):
    # pylint: disable=unused-argument
    """ handle ctrl-c """
    tty.tcsetattr(sys.stdin, tty.TCSANOW, TTY_SETTINGS)
    print(f"{Cursor('E',1)}")
    print(f'\n{"Exiting spy.py...":<80}\n')
    if _WITH_GEOIP and geoip2_enable:
        GEOIP2_CLIENT.close()
    sys.exit(0)


def fmt_html() -> str:
    """ return string with users/totals as html """
    reponse = ""
    for u in get_users():
        reponse += f"{u.name}/{u.group}<br>\n"
        reponse += f"tagline: {u.get('tagline')}<br>\n"
        reponse += f"host: ({u.get('host')})<br>\n"
        reponse += f"status: {u.status}<br><br>\n\n"
    reponse += f"currently {str(User.onlineusers)} users of {MAXUSERS} users online<br>\n"
    reponse += f"up: {User.uploads} {conv_speed(User.total_up_speed)}, "
    reponse += f"down: {User.downloads} {conv_speed(User.total_dn_speed)}, "
    reponse += f"total: {User.total} {conv_speed(User.total_speed)}<br>\n"
    reponse += f"{str(User.browsers)} browser(s), {str(User.idlers)} idler(s)<br>\n"
    return reponse


# TODO: improve handling 'no users logged in'
def create_app() -> object:
    """ create flask app with routes """
    if _WITH_FLASK and FLASK_MODE == 1:
        tmpl_path = os.path.join(SCRIPTDIR, 'templates/')
        static_path = os.path.join(SCRIPTDIR, 'static/')
        app = flask.Flask(__name__, template_folder=tmpl_path, static_folder=static_path)
        #app.secret_key = 'SECRET_KEY'

        @app.route('/')
        def index():
            return flask.redirect('/spy')
        @app.route('/favicon.ico')
        def favicon():
            return ''
        @app.route('/html')
        def html():
            return fmt_html()
        @app.route('/spy', defaults={'route': 'spy'}, methods=['POST', 'GET'])
        @app.route('/users', defaults={'route': 'users'}, methods=['POST', 'GET'])
        @app.route('/totals', defaults={'route': 'totals'}, methods=['POST', 'GET'])
        def webspy(route):
            #return flask.render_template(f'{flask.request.path}.html',
            try:
                users = get_users()
            except RuntimeError:
                users = None
            sort_attr = flask.request.args.get('sort_attr', default='username', type=str)
            if flask.request.args.get('sort_attr') == '' and bool(flask.request.args.get('sort_rev')):
                sort_attr = 'name'
            return flask.render_template(f'{route}.html',
                users = users,
                totalusers = TOTALUSERS,
                glftpd_version = GL_VER,
                spy_version = SPY_FULLVER,
                sort_attr = sort_attr,
                sort_rev = flask.request.args.get('sort_rev', default=False, type=bool),
                uniq_attr = flask.request.args.get('uniq_attr', default=None, type=str)
            )
        @app.route('/user/<username>')
        def user(username):
            r = get_userfile(username)
            status = r.get('status')
            if status == "Success":
                return r.get('result'), 200
            elif status == "FileNotFound":
                return [status], 500
            elif status == "UserNotFound":
                return [status], 404

        @app.route('/kick/<username>')
        def kick(username):
            r = kill_procid(username, get_users())
            status = r.get('status')
            if status == "Success":
                return [f"{status}: killed user"], 200
            elif status == "NotFound":
                return [status], 404
            elif status == "NotRoot":
                return [r.get('error')], 500
            else:
                return ["Unknown"], 500
        return app


# main
#######

def main():
    """ start flask, http.server or cli """
    if _WITH_FLASK and FLASK_MODE == 1:
        APP.run(**FLASK_OPTIONS)
    elif _WITH_HTTPD and HTTPD_MODE == 1:
        print('Starting http server thread...')
        t = TCPServerThread()
        t.daemon = True
        try:
            t.start()
            t.join()
        except (KeyboardInterrupt, SystemExit):
            print('HTTPD mode exiting...')
        finally:
            exit(0)
    else:
        cli()


APP = create_app()

if __name__ == "__main__":
    main()

# fuquallkthnxbye.
