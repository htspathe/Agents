import json
import os
import shutil
import subprocess
import wave
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import hybrid_video as hv
import hybrid_compute as hc

class HybridTests(unittest.TestCase):
    def test_checkpoint_reuses_only_verified_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            manifest={"steps":{}}
            target=folder/"audio.wav"
            calls=[]
            def produce(path):
                calls.append(1)
                path.write_bytes(b"audio")
            hv._stage(manifest,folder,"voice",["script"],target,produce)
            hv._stage(manifest,folder,"voice",["script"],target,produce)
            self.assertEqual(len(calls),1)
            target.write_bytes(b"corrupt")
            manifest["steps"]["assembly"]={"input":"old"}
            hv._stage(manifest,folder,"voice",["script"],target,produce)
            self.assertEqual(len(calls),2)
            self.assertNotIn("assembly",manifest["steps"])

    def test_failed_stage_has_no_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            manifest={"steps":{}}
            def fail(path):
                path.write_bytes(b"partial")
                raise RuntimeError("failed")
            with self.assertRaises(RuntimeError):
                hv._stage(manifest,folder,"voice",[],folder/"audio.wav",fail)
            self.assertFalse((folder/"audio.part.wav").exists())
            self.assertEqual(manifest["steps"],{})

    def test_no_remote_llm_or_unconfirmed_cloud(self):
        with patch.dict(os.environ,{"PRODUCTION_LOCAL_LLM_URL":"https://example.com/v1","PRODUCTION_LOCAL_LLM_OFFLINE_CONFIRMED":"1","PRODUCTION_KAGGLE_NONCOMMERCIAL_CONFIRMED":"0"}):
            with self.assertRaises(ValueError):
                hc.local_narration("test subject")
            with self.assertRaises(ValueError):
                hc.kaggle_command("status","user/notebook")

    def test_srt_timestamp(self):
        self.assertEqual(hv._srt_time(61.234),"00:01:01,234")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and FFprobe required")
    def test_real_ffmpeg_assembly(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            scene=folder/"scene_00.mp4"
            subprocess.run([shutil.which("ffmpeg"),"-y","-v","error","-f","lavfi","-i","color=c=blue:s=320x240:r=24:d=2","-c:v","libx264","-threads","2",str(scene)],check=True,capture_output=True,timeout=30)
            with wave.open(str(folder/"narration.wav"),"wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\x00\x00"*16000)
            (folder/"subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n",encoding="utf-8")
            final=folder/"final.mp4"
            hv._assemble(folder,[{"file":scene.name}],"9:16",final)
            data,duration=hv._probe(final)
            self.assertLess(abs(duration-1),0.5)
            self.assertEqual({s["codec_type"] for s in data["streams"]},{"video","audio"})
            video=next(s for s in data["streams"] if s["codec_type"]=="video")
            self.assertEqual((video["width"],video["height"]),(720,1280))

    def test_invalid_scene_ids_blocked(self):
        with patch.object(hv.media_factory,"output_path",return_value=None):
            with self.assertRaises(ValueError):
                hv.create_from_media({"scene_ids":["123456789012"]})

if __name__=="__main__":
    unittest.main()
