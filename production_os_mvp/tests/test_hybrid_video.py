import json
import os
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

    def test_invalid_scene_ids_blocked(self):
        with patch.object(hv.media_factory,"output_path",return_value=None):
            with self.assertRaises(ValueError):
                hv.create_from_media({"scene_ids":["123456789012"]})

if __name__=="__main__":
    unittest.main()
