import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import scene_director as director
import media_factory as media
import hybrid_video as hybrid
import hybrid_compute as compute


class DirectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [
            patch.object(director, "OUTPUT", self.root/"director"),
            patch.object(media, "RUNTIME", self.root/"runtime"),
            patch.object(media, "DB_PATH", self.root/"runtime"/"media.db"),
            patch.object(media, "OUTPUT_DIR", self.root/"media"),
            patch.dict(os.environ, {"FREELLMAPI_FREE_VIDEO_MODELS":"test-video"}, clear=False),
        ]
        for p in self.patches:
            p.start()
        director._STOP.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def request(self, prompt="A cinematic landscape at dawn"):
        return {"provider":"freellmapi","model":"test-video","prompt":prompt,
                "duration":6,"aspect_ratio":"9:16"}

    def test_storyboard_queue_is_idempotent(self):
        status={"providers":{"freellmapi":{"ready":True,"configured_models":["test-video"]}}}
        with patch.object(media,"provider_status",return_value=status):
            first=media.enqueue_storyboard("123456789abc",[self.request(),self.request("City at sunset in cinematic style")])
            second=media.enqueue_storyboard("123456789abc",[self.request(),self.request("City at sunset in cinematic style")])
            self.assertEqual(first,second)
            self.assertEqual(len(media.list_jobs()),2)
            with self.assertRaises(ValueError):
                media.enqueue_storyboard("123456789abc",[self.request("Different cinematic prompt")])

    def test_storyboard_queue_does_not_retry_failed_job(self):
        status={"providers":{"freellmapi":{"ready":True,"configured_models":["test-video"]}}}
        with patch.object(media,"provider_status",return_value=status):
            ids=media.enqueue_storyboard("123456789abc",[self.request()])
            media._update_job(ids[0],status="failed")
            self.assertEqual(media.enqueue_storyboard("123456789abc",[self.request()]),ids)
            self.assertEqual(media.get_job(ids[0])["status"],"failed")

    def test_full_orchestration_waits_and_reuses_voice(self):
        job=director.create({"topic":"Sujet de test","model":"test-video","scene_duration":6,"max_scenes":3})
        calls=[]
        def voice(text, output):
            calls.append(text)
            with wave.open(str(output),"wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(b"\x00\x00"*8000*8)
        prompts=["Cinematic shot of a city at dawn","Cinematic shot of a forest at dusk"]
        status={"providers":{"freellmapi":{"ready":True,"configured_models":["test-video"]}}}
        with patch.object(director,"_preflight"), \
             patch.object(director.piper_tts,"get_settings",return_value={"voice":"fr_FR-siwis-medium"}), \
             patch.object(director.piper_tts,"synthesize_to_wav",side_effect=voice), \
             patch.object(compute,"local_narration",return_value="Une narration française assez longue."), \
             patch.object(compute,"local_storyboard",return_value=prompts) as storyboard, \
             patch.object(media,"provider_status",return_value=status), \
             patch.object(hybrid,"create") as create, \
             patch.object(hybrid,"run",return_value={"status":"awaiting_review"}):
            job=director.advance(job)
            self.assertEqual(job["status"],"waiting_scenes")
            self.assertEqual(len(job["scene_ids"]),2)
            storyboard.assert_called_once_with(job["narration"],2,"9:16")
            job=director.advance(director.get(job["id"]))
            self.assertEqual(len(calls),1)
            for scene in job["scene_ids"]:
                media.OUTPUT_DIR.mkdir(exist_ok=True)
                (media.OUTPUT_DIR/(scene+".mp4")).write_bytes(b"fake-media-fixture")
                media._update_job(scene,status="completed",output_name=scene+".mp4")
            job=director.advance(director.get(job["id"]))
            self.assertEqual(job["status"],"awaiting_review")
            self.assertEqual(len(calls),1)
            create.assert_called_once()
            self.assertEqual(create.call_args.kwargs["job_id"],job["id"])
            self.assertEqual(len(media.list_jobs()),2)

    def test_llm_storyboard_requires_exact_count(self):
        with patch.object(compute,"_local_completion",return_value='["Only one prompt here"]'):
            with self.assertRaises(ValueError):
                compute.local_storyboard("Narration",2,"9:16")


if __name__=="__main__":
    unittest.main()
