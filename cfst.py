#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cfst.py — Cloudflare 优选 IP 测速（Python 版，仅 IPv4）

功能对标 XIU2/CloudflareSpeedTest 的核心流程：
  1. 延迟测速（TCPing，多线程，默认 443 端口）
  2. 按 延迟/丢包 条件过滤并排序
  3. 从最低延迟起逐个下载测速（HTTPS 请求直连 IP + SNI 域名，等效 curl --resolve）
  4. 输出结果到终端和 result.csv

设计目标：只依赖 Python 标准库，可在 iOS a-Shell / 任意 Python3 环境运行。
"""

import argparse
import csv
import ipaddress
import random
import socket
import ssl
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

VERSION = "v1.1.0"

# Cloudflare 官方 IPv4 段（https://www.cloudflare.com/ips/）
CF_CIDRS = [
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/12",
    "172.64.0.0/13",
    "131.0.72.0/22",
]

DEFAULT_URL = "https://speed.cloudflare.com/__down?bytes=200000000"

_print_lock = threading.Lock()


def sprint(text):
    with _print_lock:
        sys.stdout.write(text)
        sys.stdout.flush()


def load_cidrs(args):
    if args.ip:
        return [x.strip() for x in args.ip.split(",") if x.strip()]
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    return list(CF_CIDRS)


def sample_ips(cidrs, allip):
    """与 CFST 一致：默认每个 /24 段随机抽 1 个 IP；-allip 测全部。"""
    ips = []
    for c in cidrs:
        net = ipaddress.ip_network(c)
        if net.prefixlen < 24:
            subs = net.subnets(new_prefix=24)
        else:
            subs = [net]
        for s in subs:
            base = int(s.network_address)
            count = s.num_addresses
            if allip:
                ips.extend(str(ipaddress.ip_address(base + i)) for i in range(count))
            else:
                ips.append(str(ipaddress.ip_address(base + random.randint(0, count - 1))))
    return ips


def tcp_ping(ip, port, count, timeout):
    """返回 (ip, sent, recv, avg_ms)。完全超时的 avg 为 0。"""
    recv = 0
    total = 0.0
    for _ in range(count):
        t0 = time.perf_counter()
        try:
            s = socket.create_connection((ip, port), timeout=timeout)
            s.close()
            recv += 1
            total += (time.perf_counter() - t0) * 1000.0
        except OSError:
            pass
    avg = total / recv if recv else 0.0
    return (ip, count, recv, avg, "N/A")


def http_ping(ip, port, use_tls, host, path, ssl_ctx, count, timeout, valid_codes):
    """HTTPing：完整发一次 HTTP(S) 请求，测量到响应头接收完成的耗时。"""
    recv = 0
    total = 0.0
    colo = "N/A"
    req = ("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: cfst.py/%s\r\n"
           "Accept: */*\r\nConnection: close\r\n\r\n" % (path, host, VERSION)).encode("ascii")
    for _ in range(count):
        t0 = time.perf_counter()
        sock = None
        try:
            sock = socket.create_connection((ip, port), timeout=timeout)
            sock.settimeout(timeout)
            if use_tls:
                sock = ssl_ctx.wrap_socket(sock, server_hostname=host)
            sock.sendall(req)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(8192)
                if not chunk:
                    raise OSError("connection closed before headers")
                buf += chunk
            elapsed = (time.perf_counter() - t0) * 1000.0
            head = buf.partition(b"\r\n\r\n")[0]
            lines = head.split(b"\r\n")
            try:
                code = int(lines[0].split()[1])
            except (IndexError, ValueError):
                raise OSError("bad status line")
            if code not in valid_codes:
                raise OSError("HTTP status %d" % code)
            recv += 1
            total += elapsed
            for ln in lines[1:]:
                if ln.lower().startswith(b"cf-ray:"):
                    colo = ln.split(b"-")[-1].decode("ascii", "replace").strip()
        except OSError:
            pass
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
    avg = total / recv if recv else 0.0
    return (ip, count, recv, avg, colo)


def latency_stage(ips, args):
    results = []
    done = 0
    usable = 0
    total = len(ips)
    if args.httping:
        scheme, rest = args.url.split("://", 1)
        hostport, _, path = rest.partition("/")
        path = "/" + path if path else "/"
        host = hostport.split(":")[0]
        use_tls = scheme == "https"
        ssl_ctx = ssl.create_default_context()
        valid_codes = {args.httping_code} if args.httping_code is not None else {200, 301, 302}
        sprint("开始延迟测速（模式：HTTPing, 端口：%d, 线程：%d, 次数：%d, 地址：%s）\n"
               % (args.tp, args.n, args.t, args.url))

        def ping(ip):
            return http_ping(ip, args.tp, use_tls, host, path, ssl_ctx,
                             args.t, args.timeout, valid_codes)
    else:
        sprint("开始延迟测速（模式：TCP, 端口：%d, 线程：%d, 次数：%d）\n"
               % (args.tp, args.n, args.t))

        def ping(ip):
            return tcp_ping(ip, args.tp, args.t, args.timeout)

    with ThreadPoolExecutor(max_workers=args.n) as pool:
        futures = {pool.submit(ping, ip): ip for ip in ips}
        for fut in as_completed(futures):
            r = fut.result()
            done += 1
            if r[2] > 0:
                usable += 1
                results.append(r)
            if done % 25 == 0 or done == total:
                sprint("\r进度: %d / %d  可用: %d   " % (done, total, usable))
    sprint("\n")
    # 丢包率越低越优先，其次平均延迟（与 CFST 排序思路一致）
    results.sort(key=lambda r: (1.0 - r[2] / r[1], r[3]))
    return results


def passes_filters(r, args):
    _, sent, recv, avg, _ = r
    loss = 1.0 - recv / sent
    if loss > args.tlr:
        return False
    if avg > args.tl or avg < args.tll:
        return False
    return True


def download_speed(ip, port, url, duration, timeout):
    """直连 IP 发 HTTP(S) 请求（SNI/Host 用域名），返回 (MB/s, colo)。"""
    scheme, rest = url.split("://", 1)
    hostport, _, path = rest.partition("/")
    path = "/" + path if path else "/"
    host = hostport.split(":")[0]
    use_tls = scheme == "https"

    colo = "N/A"
    sock = socket.create_connection((ip, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        if use_tls:
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)
        req = ("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: cfst.py/%s\r\n"
               "Accept: */*\r\n\r\n" % (path, host, VERSION))
        sock.sendall(req.encode("ascii"))

        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(8192)
            if not chunk:
                raise OSError("connection closed before headers")
            buf += chunk
        head, _, body = buf.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        try:
            code = int(lines[0].split()[1])
        except (IndexError, ValueError):
            raise OSError("bad status line: %r" % lines[0])
        if code >= 400:
            raise OSError("HTTP status %d" % code)
        for ln in lines[1:]:
            if ln.lower().startswith(b"cf-ray:"):
                colo = ln.split(b"-")[-1].decode("ascii", "replace").strip()

        received = len(body)
        start = time.perf_counter()
        while True:
            remaining = duration - (time.perf_counter() - start)
            if remaining <= 0:
                break
            sock.settimeout(min(timeout, remaining) + 0.5)
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            received += len(chunk)
        elapsed = time.perf_counter() - start
        return received / elapsed / 1048576.0, colo
    finally:
        try:
            sock.close()
        except OSError:
            pass


def download_stage(candidates, args):
    sprint("开始下载测速（下限：%.2f MB/s, 数量：%d, 队列：%d）\n"
           % (args.sl, args.dn, len(candidates)))
    # 下载阶段需要完成 TLS 握手 + 收响应头，1s 超时对高延迟 IP 太短
    dl_timeout = max(args.timeout, 4.0)
    qualified = []
    for r in candidates:
        ip = r[0]
        try:
            speed, colo = download_speed(ip, args.tp, args.url, args.dt, dl_timeout)
        except (OSError, ssl.SSLError) as e:
            if args.debug:
                sprint("[%s] 下载测速失败: %s\n" % (ip, e))
            speed, colo = 0.0, "N/A"
        row = (ip, r[1], r[2], 1.0 - r[2] / r[1], r[3], speed, colo)
        if speed >= args.sl:
            qualified.append(row)
            sprint("\r下载测速进度: %d / %d   " % (len(qualified), args.dn))
            if len(qualified) >= args.dn:
                break
        elif args.sl <= 0:
            qualified.append(row)
    sprint("\n")
    qualified.sort(key=lambda x: -x[5])
    return qualified


def latency_only_rows(results):
    rows = [(ip, sent, recv, 1.0 - recv / sent, avg, 0.0, "N/A")
            for (ip, sent, recv, avg) in results]
    rows.sort(key=lambda x: x[4])
    return rows


def print_rows(rows, limit):
    if limit <= 0:
        return
    sprint("%-16s %-6s %-6s %-8s %-10s %-14s %s\n"
           % ("IP 地址", "已发送", "已接收", "丢包率", "平均延迟", "下载速度(MB/s)", "地区码"))
    for r in rows[:limit]:
        sprint("%-16s %-6d %-6d %-8.2f %-10.2f %-14.2f %s\n"
               % (r[0], r[1], r[2], r[3], r[4], r[5], r[6]))


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["IP 地址", "已发送", "已接收", "丢包率", "平均延迟", "下载速度(MB/s)", "地区码"])
        for r in rows:
            w.writerow([r[0], r[1], r[2], "%.2f" % r[3], "%.2f" % r[4], "%.2f" % r[5], r[6]])


def build_parser():
    p = argparse.ArgumentParser(
        prog="cfst.py",
        description="CloudflareSpeedTest %s — 测试 Cloudflare CDN 所有 IPv4 的延迟和速度，获取最快 IP" % VERSION)
    p.add_argument("-n", type=int, default=100, help="延迟测速线程（默认 100；手机建议 50~200）")
    p.add_argument("-t", type=int, default=4, help="单个 IP 延迟测速次数（默认 4）")
    p.add_argument("-tp", type=int, default=443, help="测速端口（默认 443）")
    p.add_argument("-timeout", type=float, default=1.0, help="单次连接超时秒数（默认 1.0）")
    p.add_argument("-httping", action="store_true", help="延迟测速模式改为 HTTP 协议（测速地址为 -url）")
    p.add_argument("-httping-code", type=int, default=None,
                   help="HTTPing 有效状态码，仅限一个（默认 200 301 302）")
    p.add_argument("-cfcolo", default=None,
                   help="匹配指定地区码，逗号分隔如 HKG,NRT,LAX（仅 HTTPing 模式可用）")
    p.add_argument("-dn", type=int, default=10, help="下载测速数量（默认 10）")
    p.add_argument("-dt", type=float, default=10.0, help="单个 IP 下载测速秒数（默认 10）")
    p.add_argument("-url", default=DEFAULT_URL, help="下载测速地址（默认 Cloudflare 官方测速）")
    p.add_argument("-tl", type=float, default=9999.0, help="平均延迟上限 ms（默认 9999）")
    p.add_argument("-tll", type=float, default=0.0, help="平均延迟下限 ms（默认 0）")
    p.add_argument("-tlr", type=float, default=1.0, help="丢包几率上限 0.00~1.00（默认 1.00）")
    p.add_argument("-sl", type=float, default=0.0, help="下载速度下限 MB/s（默认 0）")
    p.add_argument("-p", type=int, default=10, help="终端显示结果数量，0 为不显示（默认 10）")
    p.add_argument("-f", dest="file", default=None, help="IP 段数据文件（默认内置 Cloudflare IPv4 段）")
    p.add_argument("-ip", default=None, help="直接指定 IP/段，逗号分隔，如 1.1.1.1,2.2.2.2/24")
    p.add_argument("-o", default="result.csv", help='结果文件，空字符串不写入（默认 result.csv）')
    p.add_argument("-dd", action="store_true", help="禁用下载测速，结果按延迟排序")
    p.add_argument("-allip", action="store_true", help="测速 IP 段中的每个 IP（默认每 /24 随机 1 个）")
    p.add_argument("-debug", action="store_true", help="调试模式，输出下载测速失败原因")
    p.add_argument("-v", action="version", version="cfst.py " + VERSION)
    return p


def main():
    args = build_parser().parse_args()
    cidrs = load_cidrs(args)
    ips = sample_ips(cidrs, args.allip)
    sprint("cfst.py %s（仅 IPv4）共 %d 个待测 IP\n" % (VERSION, len(ips)))

    results = latency_stage(ips, args)
    filtered = [r for r in results if passes_filters(r, args)]
    if args.cfcolo:
        colos = {c.strip().upper() for c in args.cfcolo.split(",") if c.strip()}
        if not args.httping:
            sprint("注意：-cfcolo 仅在 HTTPing 模式下有效，已忽略。\n")
        else:
            filtered = [r for r in filtered if r[4].upper() in colos]
    if not filtered:
        sprint("没有找到满足条件的 IP。\n")
        return

    if args.dd:
        rows = latency_only_rows(filtered)
    else:
        rows = download_stage(filtered, args)
        if not rows:
            sprint("没有找到满足下载速度条件的 IP（可加 -debug 排查或调低 -sl）。\n")
            return

    print_rows(rows, args.p)
    if args.o.strip():
        write_csv(rows, args.o)
        sprint("完整测速结果已写入 %s\n" % args.o)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sprint("\n已中断。\n")
