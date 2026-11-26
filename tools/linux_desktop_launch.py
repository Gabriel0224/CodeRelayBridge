"""Launch the Linux desktop independently of the calling terminal."""
import ast
import os
import subprocess
import sys

def desktop_command(argv, environment=None):
    env = dict(os.environ if environment is None else environment)
    args = list(argv)
    try:
        reply = subprocess.check_output(['/usr/bin/gdbus', 'call', '--session', '--dest', 'org.a11y.Bus', '--object-path', '/org/a11y/bus', '--method', 'org.a11y.Bus.GetAddress'], stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, timeout=3, env=env)
        address, = ast.literal_eval(reply)
        if isinstance(address, str) and address.startswith('unix:'):
            env.update(AT_SPI_BUS_ADDRESS=address, GTK_A11Y='atspi', GNOME_ACCESSIBILITY='1', NO_AT_BRIDGE='0')
            modules = [m for m in env.get('GTK_MODULES', '').split(':') if m]
            if 'atk-bridge' not in modules:
                modules.append('atk-bridge')
            env['GTK_MODULES'] = ':'.join(modules)
            if '--force-renderer-accessibility' not in args:
                args.insert(1, '--force-renderer-accessibility')
    except (OSError, subprocess.SubprocessError, SyntaxError, ValueError, TypeError):
        pass
    return (args, env)

def launch(argv):
    args, env = desktop_command(argv)
    return subprocess.Popen(['/usr/bin/systemd-cat', '--identifier=crb-codex-desktop', '--', *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True, env=env)
if __name__ == '__main__':
    launch(['/usr/bin/chatgpt', *sys.argv[1:]])
