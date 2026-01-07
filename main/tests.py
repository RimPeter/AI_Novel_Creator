from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from .models import Character, NovelProject, OutlineNode
from .llm import LLMResult
from .models import Location


class MoveSceneTests(TestCase):
    def setUp(self):
        self.project = NovelProject.objects.create(
            title="Test Project",
            slug="test-project",
            target_word_count=1000,
        )
        self.act = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.ACT,
            parent=None,
            order=1,
            title="Act I",
        )
        self.chapter_a = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.CHAPTER,
            parent=self.act,
            order=1,
            title="Chapter 1",
        )
        self.chapter_b = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.CHAPTER,
            parent=self.act,
            order=2,
            title="Chapter 2",
        )
        self.scene_1 = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter_a,
            order=1,
            title="Scene 1",
        )
        self.scene_2 = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter_a,
            order=2,
            title="Scene 2",
        )
        self.scene_b1 = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter_b,
            order=1,
            title="Scene 3",
        )

    def test_move_scene_to_other_chapter_appends(self):
        url = reverse("scene-move", kwargs={"slug": self.project.slug})
        resp = self.client.post(
            url,
            data={
                "scene_id": str(self.scene_1.id),
                "target_chapter_id": str(self.chapter_b.id),
            },
        )
        self.assertEqual(resp.status_code, 302)

        self.scene_1.refresh_from_db()
        self.scene_2.refresh_from_db()
        self.scene_b1.refresh_from_db()

        self.assertEqual(self.scene_1.parent_id, self.chapter_b.id)
        self.assertEqual(self.scene_b1.order, 1)
        self.assertEqual(self.scene_1.order, 2)
        self.assertEqual(self.scene_2.order, 1)

    def test_move_scene_before_other_scene(self):
        url = reverse("scene-move", kwargs={"slug": self.project.slug})
        resp = self.client.post(
            url,
            data={
                "scene_id": str(self.scene_2.id),
                "target_chapter_id": str(self.chapter_b.id),
                "before_scene_id": str(self.scene_b1.id),
            },
        )
        self.assertEqual(resp.status_code, 302)

        self.scene_2.refresh_from_db()
        self.scene_b1.refresh_from_db()

        self.assertEqual(self.scene_2.parent_id, self.chapter_b.id)
        self.assertEqual(self.scene_2.order, 1)
        self.assertEqual(self.scene_b1.order, 2)

    def test_reorder_scene_within_chapter(self):
        url = reverse("scene-move", kwargs={"slug": self.project.slug})
        resp = self.client.post(
            url,
            data={
                "scene_id": str(self.scene_2.id),
                "target_chapter_id": str(self.chapter_a.id),
                "before_scene_id": str(self.scene_1.id),
            },
        )
        self.assertEqual(resp.status_code, 302)

        self.scene_1.refresh_from_db()
        self.scene_2.refresh_from_db()

        self.assertEqual(self.scene_2.parent_id, self.chapter_a.id)
        self.assertEqual(self.scene_2.order, 1)
        self.assertEqual(self.scene_1.order, 2)

    def test_ajax_move_returns_json(self):
        url = reverse("scene-move", kwargs={"slug": self.project.slug})
        resp = self.client.post(
            url,
            data={
                "scene_id": str(self.scene_1.id),
                "target_chapter_id": str(self.chapter_b.id),
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/json")
        self.assertEqual(resp.json(), {"ok": True})


class SceneStructurizeRenderTests(TestCase):
    def setUp(self):
        self.project = NovelProject.objects.create(
            title="Test Project",
            slug="test-project",
            target_word_count=1000,
        )
        self.act = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.ACT,
            parent=None,
            order=1,
            title="Act I",
        )
        self.chapter = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.CHAPTER,
            parent=self.act,
            order=1,
            title="Chapter 1",
            summary="Chapter summary.",
        )
        self.scene = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter,
            order=1,
            title="Scene 1",
            summary="A tense meeting sets the stakes. A secret surfaces.",
            pov="Ava",
            location="Docking bay",
        )

    def test_structurize_fills_structure_json(self):
        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        resp = self.client.post(
            url,
            data={
                "order": 1,
                "title": self.scene.title,
                "summary": self.scene.summary,
                "pov": self.scene.pov,
                "location": self.scene.location,
                "action": "structurize",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.scene.refresh_from_db()
        self.assertTrue(self.scene.structure_json.strip())

    def test_render_uses_llm_when_available(self):
        self.scene.structure_json = (
            '{\n  "schema_version": 1,\n  "title": "Scene 1",\n  "summary": "x",\n  "pov": "Ava",\n  "location": "Docking bay",\n  "beats": []\n}'
        )
        self.scene.save(update_fields=["structure_json"])

        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        with patch("main.views.call_llm", return_value=LLMResult(text="Prose text.", usage={"ok": True})):
            resp = self.client.post(
                url,
                data={
                    "order": 1,
                    "title": self.scene.title,
                    "summary": self.scene.summary,
                    "pov": self.scene.pov,
                    "location": self.scene.location,
                    "structure_json": self.scene.structure_json,
                    "rendered_text": "",
                    "action": "render",
                },
            )
        self.assertEqual(resp.status_code, 302)
        self.scene.refresh_from_db()
        self.assertIn("Prose text.", self.scene.rendered_text)


class CharacterViewsTests(TestCase):
    def setUp(self):
        self.project_a = NovelProject.objects.create(
            title="Project A",
            slug="project-a",
            target_word_count=1000,
        )
        self.project_b = NovelProject.objects.create(
            title="Project B",
            slug="project-b",
            target_word_count=1000,
        )
        self.char_a1 = Character.objects.create(project=self.project_a, name="Ava", role="Protagonist", age=22, gender="Female")
        self.char_a2 = Character.objects.create(project=self.project_a, name="Zed", role="Antagonist")
        self.char_b1 = Character.objects.create(project=self.project_b, name="Bryn", role="Sidekick")

    def test_list_scoped_to_project(self):
        url = reverse("character-list", kwargs={"slug": self.project_a.slug})
        resp = self.client.get(url)
        self.assertContains(resp, "Ava")
        self.assertContains(resp, "Zed")
        self.assertNotContains(resp, "Bryn")

    def test_search_filters(self):
        url = reverse("character-list", kwargs={"slug": self.project_a.slug})
        resp = self.client.get(url, {"q": "ava"})
        self.assertContains(resp, "Ava")
        self.assertNotContains(resp, "Zed")

    def test_edit_is_project_scoped(self):
        url = reverse("character-edit", kwargs={"slug": self.project_a.slug, "pk": self.char_b1.id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_brainstorm_returns_suggestions_for_empty_fields_only(self):
        url = reverse("character-brainstorm", kwargs={"slug": self.project_a.slug})
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text='{"age": 30, "gender": "Male", "name": "SHOULD_NOT_OVERWRITE"}',
                usage={"ok": True},
            ),
        ):
            resp = self.client.post(
                url,
                data={
                    "name": "Ava",
                    "role": "",
                    "age": "",
                    "gender": "",
                    "personality": "",
                    "appearance": "",
                    "background": "",
                    "goals": "",
                    "voice_notes": "",
                    "description": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "suggestions": {"age": 30, "gender": "Male"}})

    def test_add_details_does_not_return_name_and_can_enhance_fields(self):
        url = reverse("character-add-details", kwargs={"slug": self.project_a.slug})
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text='{"name": "NOPE", "personality": "Adds a subtle tell: taps her ring when lying."}',
                usage={"ok": True},
            ),
        ):
            resp = self.client.post(
                url,
                data={
                    "name": "Ava",
                    "role": "Protagonist",
                    "age": "22",
                    "gender": "Female",
                    "personality": "Driven and guarded.",
                    "appearance": "",
                    "background": "",
                    "goals": "",
                    "voice_notes": "",
                    "description": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "suggestions": {"personality": "Adds a subtle tell: taps her ring when lying."}})


class LocationViewsTests(TestCase):
    def setUp(self):
        self.project_a = NovelProject.objects.create(title="Project A", slug="project-a", target_word_count=1000)
        self.project_b = NovelProject.objects.create(title="Project B", slug="project-b", target_word_count=1000)
        self.loc_a = Location.objects.create(project=self.project_a, name="Docking Bay", objects_map={"crate": "sealed"})
        self.loc_b = Location.objects.create(project=self.project_b, name="Garden", objects_map={})

    def test_list_scoped_to_project(self):
        url = reverse("location-list", kwargs={"slug": self.project_a.slug})
        resp = self.client.get(url)
        self.assertContains(resp, "Docking Bay")
        self.assertNotContains(resp, "Garden")

    def test_create_parses_object_pairs(self):
        url = reverse("location-create", kwargs={"slug": self.project_a.slug})
        resp = self.client.post(
            url,
            data={
                "name": "Market",
                "description": "Busy and loud.",
                "object_key": ["stall", "lamp"],
                "object_value": ["fruit vendor", "flickering neon"],
            },
        )
        self.assertEqual(resp.status_code, 302)
        loc = Location.objects.get(project=self.project_a, name="Market")
        self.assertEqual(loc.objects_map, {"stall": "fruit vendor", "lamp": "flickering neon"})

    def test_brainstorm_location_description_only_when_empty(self):
        url = reverse("location-brainstorm", kwargs={"slug": self.project_a.slug})
        with patch("main.views.call_llm") as mocked:
            resp = self.client.post(
                url,
                data={
                    "name": "Docking Bay",
                    "description": "Already here.",
                    "object_key": ["crate"],
                    "object_value": ["sealed"],
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "suggestions": {}})
        mocked.assert_not_called()

    def test_brainstorm_location_description_returns_suggestion(self):
        url = reverse("location-brainstorm", kwargs={"slug": self.project_a.slug})
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(text='{"description": "A cavernous bay of cold steel."}', usage={"ok": True}),
        ):
            resp = self.client.post(
                url,
                data={
                    "name": "Docking Bay",
                    "description": "",
                    "object_key": ["crate"],
                    "object_value": ["sealed"],
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {"ok": True, "suggestions": {"description": "A cavernous bay of cold steel."}},
        )

    def test_add_location_details_returns_suggestion(self):
        url = reverse("location-add-details", kwargs={"slug": self.project_a.slug})
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(text='{"description": "Overhead, warning lights stutter red."}', usage={"ok": True}),
        ):
            resp = self.client.post(
                url,
                data={
                    "name": "Docking Bay",
                    "description": "A cavernous bay of cold steel.",
                    "object_key": ["crate"],
                    "object_value": ["sealed"],
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "suggestions": {"description": "Overhead, warning lights stutter red."}})

    def test_add_location_details_noop_when_duplicate(self):
        url = reverse("location-add-details", kwargs={"slug": self.project_a.slug})
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(text='{"description": "Overhead, warning lights stutter red."}', usage={"ok": True}),
        ):
            resp = self.client.post(
                url,
                data={
                    "name": "Docking Bay",
                    "description": "Overhead, warning lights stutter red.",
                    "object_key": [],
                    "object_value": [],
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "suggestions": {}})

    def test_extract_location_objects_requires_description(self):
        url = reverse("location-extract-objects", kwargs={"slug": self.project_a.slug})
        resp = self.client.post(
            url,
            data={"name": "Docking Bay", "description": "", "object_key": [], "object_value": []},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["ok"], False)

    def test_extract_location_objects_returns_new_objects_only(self):
        url = reverse("location-extract-objects", kwargs={"slug": self.project_a.slug})
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text='{"objects": {"crate": "sealed", "forklift": "rust-stained, idling near the bulkhead"}}',
                usage={"ok": True},
            ),
        ):
            resp = self.client.post(
                url,
                data={
                    "name": "Docking Bay",
                    "description": "A cavernous bay of cold steel. A sealed crate sits by the door.",
                    "object_key": ["crate"],
                    "object_value": ["sealed"],
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {"ok": True, "objects": {"forklift": "rust-stained, idling near the bulkhead"}},
        )
