"""
Issue #14: H.264/AAC sources must not be re-encoded; HEVC is transcoded by
default; audio is stream-copied; MP4 outputs carry +faststart.
"""
import hashlib
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import video_utils
import zip_utils

HAS_FFMPEG = shutil.which('ffmpeg') is not None and shutil.which('ffprobe') is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


def _make_video(path, vcodec='libx264', extra=(), seconds=1, color='red'):
    cmd = ['ffmpeg', '-y', '-v', 'error',
           '-f', 'lavfi', '-i', f'color=c={color}:s=320x240:d={seconds}:r=30',
           '-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}',
           '-c:v', vcodec, *extra, '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '128k', '-shortest', str(path)]
    subprocess.run(cmd, check=True)
    return path


def _packet_hash(path, stream):
    """Hash of the raw compressed packets - equal means no re-encode."""
    out = subprocess.run(
        ['ffmpeg', '-v', 'error', '-i', str(path), '-map', f'0:{stream}:0',
         '-c', 'copy', '-f', 'hash', '-hash', 'md5', '-'],
        capture_output=True, text=True, check=True).stdout
    return out.strip()


def _moov_before_mdat(path):
    data = open(path, 'rb').read()
    return data.find(b'moov') < data.find(b'mdat')


@pytest.fixture(autouse=True)
def _default_policy():
    saved = dict(video_utils.VIDEO_POLICY)
    video_utils.set_video_policy(icloud_compatible=False, transcode_hevc=True)
    yield
    video_utils.VIDEO_POLICY.update(saved)


# ---- pure decision logic -------------------------------------------------

def test_decide_h264_remuxes_by_default():
    info = {'video_codec': 'h264', 'profile': 'high', 'pix_fmt': 'yuvj420p', 'color_range': 'pc'}
    assert video_utils.decide_video_action(info) == 'remux'


def test_decide_hevc_transcodes_by_default_and_respects_opt_out():
    info = {'video_codec': 'hevc'}
    assert video_utils.decide_video_action(info) == 'transcode'
    assert video_utils.decide_video_action(info, transcode_hevc=False) == 'remux'


def test_decide_icloud_mode_only_reencodes_incompatible_h264():
    high = {'video_codec': 'h264', 'profile': 'high', 'pix_fmt': 'yuv420p', 'color_range': 'tv'}
    full = {'video_codec': 'h264', 'profile': 'main', 'pix_fmt': 'yuvj420p', 'color_range': 'pc'}
    ok = {'video_codec': 'h264', 'profile': 'main', 'pix_fmt': 'yuv420p', 'color_range': 'tv'}
    assert video_utils.decide_video_action(high, icloud_compatible=True) == 'transcode'
    assert video_utils.decide_video_action(full, icloud_compatible=True) == 'transcode'
    assert video_utils.decide_video_action(ok, icloud_compatible=True) == 'remux'


def test_decide_other_codecs_transcode():
    assert video_utils.decide_video_action({'video_codec': 'vp9'}) == 'transcode'
    assert video_utils.decide_video_action({}) == 'unknown'


def test_audio_codec_args():
    assert video_utils.audio_codec_args('aac') == ['-c:a', 'copy']
    assert video_utils.audio_codec_args(None) == ['-c:a', 'copy']
    assert video_utils.audio_codec_args('opus')[:2] == ['-c:a', 'aac']


def test_mp4_output_args():
    assert video_utils.mp4_output_args('a.mp4') == ['-movflags', '+faststart', '-f', 'mp4']
    assert video_utils.mp4_output_args('a.mov') == ['-movflags', '+faststart']


def test_vlc_commands_do_not_transcode_audio():
    src = open(video_utils.__file__, encoding='utf-8').read()
    assert 'acodec=mp3' not in src


# ---- real files ------------------------------------------------------------

@needs_ffmpeg
def test_h264_high_profile_is_remuxed_bit_exact(tmp_path):
    src = _make_video(tmp_path / 'in.mp4', extra=['-profile:v', 'high'])
    vh, ah = _packet_hash(src, 'v'), _packet_hash(src, 'a')

    ok, out = video_utils.convert_hevc_to_h264(str(src), failed_dir_path=str(tmp_path / 'f'))
    assert ok
    assert _packet_hash(out, 'v') == vh, "H.264 video was re-encoded"
    assert _packet_hash(out, 'a') == ah, "AAC audio was re-encoded"
    assert _moov_before_mdat(out), "+faststart not applied"


@needs_ffmpeg
def test_in_place_remux_keeps_bitstreams(tmp_path):
    src = _make_video(tmp_path / 'in.mp4')
    vh = _packet_hash(src, 'v')
    ok, out = video_utils.convert_hevc_to_h264(str(src), output_path=str(src),
                                               failed_dir_path=str(tmp_path / 'f'))
    assert ok and os.path.samefile(out, src)
    assert _packet_hash(src, 'v') == vh


@needs_ffmpeg
def test_hevc_is_transcoded_but_audio_copied(tmp_path):
    src = _make_video(tmp_path / 'in.mp4', vcodec='libx265', extra=['-tag:v', 'hvc1', '-x265-params', 'log-level=error'])
    ah = _packet_hash(src, 'a')
    ok, out = video_utils.convert_hevc_to_h264(str(src), failed_dir_path=str(tmp_path / 'f'))
    assert ok
    info = video_utils.probe_video_codecs(out)
    assert info['video_codec'] == 'h264'
    assert _packet_hash(out, 'a') == ah, "AAC audio was re-encoded during HEVC transcode"


@needs_ffmpeg
def test_hevc_opt_out_keeps_hevc(tmp_path):
    src = _make_video(tmp_path / 'in.mp4', vcodec='libx265', extra=['-tag:v', 'hvc1', '-x265-params', 'log-level=error'])
    vh = _packet_hash(src, 'v')
    ok, out = video_utils.convert_hevc_to_h264(str(src), failed_dir_path=str(tmp_path / 'f'),
                                               transcode_hevc=False)
    assert ok and _packet_hash(out, 'v') == vh


@needs_ffmpeg
def test_icloud_mode_reencodes_high_profile(tmp_path):
    src = _make_video(tmp_path / 'in.mp4', extra=['-profile:v', 'high'])
    ok, out = video_utils.convert_hevc_to_h264(str(src), failed_dir_path=str(tmp_path / 'f'),
                                               icloud_compatible=True)
    assert ok
    assert video_utils.probe_video_codecs(out)['profile'] == 'main'


@needs_ffmpeg
def test_rotation_is_kept_as_metadata(tmp_path):
    plain = _make_video(tmp_path / 'plain.mp4')
    rotated = tmp_path / 'rot.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-display_rotation', '90', '-i', str(plain),
                    '-c', 'copy', str(rotated)], check=True)
    if video_utils._get_video_rotation(str(rotated)) not in (90, 270):
        pytest.skip("this ffmpeg can't write a display matrix")
    vh = _packet_hash(rotated, 'v')

    ok, msg = video_utils.enforce_portrait_video(str(rotated))
    assert ok and _packet_hash(rotated, 'v') == vh

    ok, out = video_utils.convert_hevc_to_h264(str(rotated), failed_dir_path=str(tmp_path / 'f'))
    assert ok
    assert _packet_hash(out, 'v') == vh
    assert video_utils._get_video_rotation(str(out)) == video_utils._get_video_rotation(str(rotated))


@needs_ffmpeg
def test_concat_matching_segments_is_lossless(tmp_path):
    a = _make_video(tmp_path / 'a.mp4', color='red')
    b = _make_video(tmp_path / 'b.mp4', color='blue')
    out = tmp_path / 'joined.mp4'
    ok, res = zip_utils.concat_video_segments([str(a), str(b)], str(out))
    assert ok, res
    # Stream copy: no new encode -> the joined audio is still the original AAC
    # packets and the duration is the sum of the parts.
    info = video_utils.probe_video_codecs(out)
    assert info['video_codec'] == 'h264' and info['audio_codec'] == 'aac'
    assert _moov_before_mdat(out)
    joined_v = _packet_hash(out, 'v')
    # A re-encode with libx264 would never reproduce identical packets of the
    # concatenation; compare against a manual concat-demuxer copy.
    lst = tmp_path / 'l.txt'
    lst.write_text(f"file '{a}'\nfile '{b}'\n")
    ref = tmp_path / 'ref.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', str(lst),
                    '-c', 'copy', str(ref)], check=True)
    assert joined_v == _packet_hash(ref, 'v')


@needs_ffmpeg
def test_concat_mismatched_segments_falls_back(tmp_path):
    a = _make_video(tmp_path / 'a.mp4')
    b = tmp_path / 'b.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x240:d=1:r=30',
                    '-f', 'lavfi', '-i', 'sine=d=1:sample_rate=22050', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-shortest', str(b)], check=True)
    out = tmp_path / 'joined.mp4'
    assert not zip_utils._concat_stream_copy([str(a), str(b)], str(out))[0]
    ok, res = zip_utils.concat_video_segments([str(a), str(b)], str(out))
    assert ok, res
    assert _moov_before_mdat(out)
