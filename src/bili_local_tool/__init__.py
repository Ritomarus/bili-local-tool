"""BiliLocal：只在本机运行的 B站直播消费数据分析工具。"""

from __future__ import annotations

import argparse
import threading
import webbrowser


def main() -> None:
    parser = argparse.ArgumentParser(prog="bili-local-tool", description="本机运行的 B站直播消费数据分析工具")
    parser.add_argument("--port", type=int, default=8765, help="本机端口，默认 8765（被占用会自动往后找）")
    parser.add_argument("--demo", action="store_true", help="演示模式：随机假数据，不登录、不联网")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()

    from .server import serve

    if args.demo:
        from .demo import DemoApp

        app = DemoApp()
    else:
        from .service import App
        from .storage import Storage, default_data_dir

        app = App(Storage(default_data_dir()))

    httpd, url = serve(app, args.port)
    print(f"BiliLocal 已启动{'（演示模式）' if args.demo else ''}：{url}")
    print(f"数据目录：{app.storage.root}")
    print("只监听本机 127.0.0.1，按 Ctrl+C 退出。")
    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        httpd.server_close()
