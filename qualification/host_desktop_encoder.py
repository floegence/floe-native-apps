"""Opt-in hardware real-driver qualification, never part of source CI.

Requires a prepared helper and existing FFmpeg only in the test environment.
The worker does not depend on FFmpeg. Decode verifies pixels and bitstream
restrictions; frame throughput is not client presentation latency.
"""
import argparse
import json
import statistics
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--helper', required=True)
parser.add_argument('--backend', choices=('nvidia-nvenc', 'rockchip-mpp'), required=True)
args = parser.parse_args()
sys.path.insert(0, args.helper)
from host_desktop_encoder import NativeEncoder

reports = []
for width, height in ((1920, 1080), (3840, 2160)):
    encoder = NativeEncoder(width, height, 60, 24000000, args.backend)
    colors = ((0, 0, 255, 255), (0, 255, 0, 255), (255, 0, 0, 255),
              (255, 255, 255, 255), (0, 0, 0, 255), (255, 255, 0, 255),
              (0, 255, 255, 255), (255, 0, 255, 255))
    row = b''.join(bytes(color) * (width // 8) for color in colors)
    reference = row * height
    pixels = reference
    if encoder.input_format == 'NV12':
        pixels = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-f', 'rawvideo', '-pix_fmt', 'bgra', '-s', f'{width}x{height}', '-i', 'pipe:0',
            '-vf', 'scale=in_range=full:out_range=limited:out_color_matrix=bt709',
            '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'nv12', 'pipe:1'],
            input=reference, stdout=subprocess.PIPE, check=True).stdout
    durations, access_units = [], []
    try:
        for index in range(120):
            started = time.monotonic()
            data = encoder.encode(pixels)
            durations.append(1000 * (time.monotonic() - started))
            if index < 3:
                access_units.append(data)
    finally:
        encoder.close()
    trace = subprocess.run(['ffmpeg', '-hide_banner', '-i', 'pipe:0', '-c', 'copy',
        '-bsf:v', 'trace_headers', '-f', 'null', '-'], input=b''.join(access_units),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stderr.decode()
    restrictions = [line for line in trace.splitlines() if 'max_num_reorder_frames' in line]
    if not restrictions or not all(line.endswith('= 0') for line in restrictions):
        raise AssertionError('decoder must receive explicit zero-reorder SPS')
    if args.backend == 'rockchip-mpp':
        levels = [line for line in trace.splitlines() if 'level_idc' in line]
        required = 42 if width == 1920 else 52
        if not levels or not all(int(line.rsplit('=', 1)[1]) >= required for line in levels):
            raise AssertionError('SPS level must cover original dimensions at 60 FPS')
    decoded = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'h264',
        '-i', 'pipe:0', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'bgra', 'pipe:1'],
        input=b''.join(access_units), stdout=subprocess.PIPE, check=True).stdout
    if len(decoded) != len(reference):
        raise AssertionError('hardware encode changed original dimensions')
    for index, color in enumerate(colors):
        center = (height // 2 * width + (index * 2 + 1) * width // 16) * 4
        if any(abs(decoded[center + i] - color[i]) > 5 for i in range(3)):
            raise AssertionError(f'color/layout round trip failed: bar {index}, {list(decoded[center:center+4])}')
    samples = sorted(durations[3:])
    reports.append({'width': width, 'height': height, 'samples': len(samples),
        'shared_upload_encode_ms_p50': statistics.median(samples),
        'shared_upload_encode_ms_p95': samples[int(len(samples) * .95)],
        'zero_reorder_sps': True, 'bt709_color_bars': True})
print(json.dumps({'measurement': 'synthetic worker round trip, not desktop latency',
    'backend': args.backend, 'reports': reports}))
