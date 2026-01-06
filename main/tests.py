from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from .models import NovelProject, OutlineNode
from .llm import LLMResult


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


class ChapterStructurizeRenderTests(TestCase):
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
            summary="A tense meeting sets the stakes. A secret surfaces.",
        )

    def test_structurize_fills_structure_json(self):
        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.chapter.id})
        resp = self.client.post(
            url,
            data={
                "order": 1,
                "title": self.chapter.title,
                "summary": self.chapter.summary,
                "action": "structurize",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.chapter.refresh_from_db()
        self.assertTrue(self.chapter.structure_json.strip())

    def test_render_uses_llm_when_available(self):
        self.chapter.structure_json = (
            '{\n  "schema_version": 1,\n  "chapter_title": "Chapter 1",\n  "chapter_summary": "x",\n  "scenes": []\n}'
        )
        self.chapter.save(update_fields=["structure_json"])

        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.chapter.id})
        with patch("main.views.call_llm", return_value=LLMResult(text="Prose text.", usage={"ok": True})):
            resp = self.client.post(
                url,
                data={
                    "order": 1,
                    "title": self.chapter.title,
                    "summary": self.chapter.summary,
                    "structure_json": self.chapter.structure_json,
                    "rendered_text": "",
                    "action": "render",
                },
            )
        self.assertEqual(resp.status_code, 302)
        self.chapter.refresh_from_db()
        self.assertIn("Prose text.", self.chapter.rendered_text)
