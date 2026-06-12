"""Simple HTTP server to serve the RottenDouban site"""
import http.server
import socketserver
import os
import webbrowser

PORT = 8080
DIRECTORY = r"C:/Users/LXJ20/Desktop/烂番茄豆瓣爬虫/site"

os.chdir(DIRECTORY)

handler = http.server.SimpleHTTPRequestHandler
with socketserver.TCPServer(("", PORT), handler) as httpd:
    url = f"http://localhost:{PORT}"
    print(f"网站服务器启动: {url}")
    print(f"服务目录: {DIRECTORY}")
    webbrowser.open(url)
    print("按 Ctrl+C 停止服务器")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("服务器已停止")
