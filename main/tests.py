import json
from datetime import datetime
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch
from urllib.parse import quote

from .models import Character, GenerationRun, NovelProject, OutlineNode, UserTextModelPreference
from .llm import LLMResult, SYSTEM_PROMPT, call_llm
from .models import HomeUpdate, Location


class AuthenticatedTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="tester",
            email="tester@example.com",
            password="password123",
        )
        self.client.force_login(self.user)


class LLMTests(TestCase):
    def test_call_llm_replaces_em_dash_and_uses_global_instruction(self):
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Wait\u2014no. Use this\u2014instead."))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
        )

        with patch("main.llm.client.chat.completions.create", return_value=fake_response) as mocked:
            result = call_llm(prompt="Test prompt", model_name="test-model", params={"temperature": 0.2, "max_tokens": 42})

        self.assertEqual(result.text, "Wait-no. Use this-instead.")
        self.assertEqual(
            result.usage,
            {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        )
        kwargs = mocked.call_args.kwargs
        messages = kwargs["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": SYSTEM_PROMPT})
        self.assertEqual(kwargs["max_completion_tokens"], 42)
        self.assertNotIn("max_tokens", kwargs)

    def test_call_llm_accepts_explicit_max_completion_tokens(self):
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Plain text."))],
            usage=SimpleNamespace(prompt_tokens=9, completion_tokens=4, total_tokens=13),
        )

        with patch("main.llm.client.chat.completions.create", return_value=fake_response) as mocked:
            call_llm(
                prompt="Test prompt",
                model_name="test-model",
                params={"temperature": 0.2, "max_tokens": 42, "max_completion_tokens": 64},
            )

        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["max_completion_tokens"], 64)
        self.assertNotIn("max_tokens", kwargs)

    def test_call_llm_omits_temperature_for_gpt5_family_models(self):
        fake_response = SimpleNamespace(
            output_text="Plain text.",
            usage=SimpleNamespace(input_tokens=9, output_tokens=4, total_tokens=13),
        )

        with patch("main.llm.client.responses.create", return_value=fake_response) as mocked:
            result = call_llm(
                prompt="Test prompt",
                model_name="gpt-5-mini",
                params={"temperature": 0.4, "max_tokens": 64},
            )

        kwargs = mocked.call_args.kwargs
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(kwargs["max_output_tokens"], 64)
        self.assertEqual(kwargs["instructions"], SYSTEM_PROMPT)
        self.assertEqual(kwargs["input"], "Test prompt")
        self.assertEqual(result.text, "Plain text.")
        self.assertEqual(result.usage, {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13})

    def test_call_llm_keeps_temperature_for_4x_models(self):
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Plain text."))],
            usage=SimpleNamespace(prompt_tokens=9, completion_tokens=4, total_tokens=13),
        )

        with patch("main.llm.client.chat.completions.create", return_value=fake_response) as mocked:
            call_llm(
                prompt="Test prompt",
                model_name="gpt-4.1-mini",
                params={"temperature": 0.4, "max_tokens": 64},
            )

        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["temperature"], 0.4)

    def test_call_llm_reads_responses_api_content_parts_when_output_text_missing(self):
        fake_response = SimpleNamespace(
            output_text="",
            output=[SimpleNamespace(content=[SimpleNamespace(text="Part one.")])],
            usage=SimpleNamespace(input_tokens=6, output_tokens=3, total_tokens=9),
        )

        with patch("main.llm.client.responses.create", return_value=fake_response):
            result = call_llm(
                prompt="Test prompt",
                model_name="o4-mini",
                params={"max_tokens": 64},
            )

        self.assertEqual(result.text, "Part one.")

    def test_call_llm_reads_responses_api_text_value_objects(self):
        fake_response = SimpleNamespace(
            output_text=None,
            output=[SimpleNamespace(content=[SimpleNamespace(text=SimpleNamespace(value="Nested text."))])],
            usage=SimpleNamespace(input_tokens=6, output_tokens=3, total_tokens=9),
        )

        with patch("main.llm.client.responses.create", return_value=fake_response):
            result = call_llm(
                prompt="Test prompt",
                model_name="gpt-5-mini",
                params={"max_tokens": 64},
            )

        self.assertEqual(result.text, "Nested text.")

    def test_call_llm_falls_back_to_chat_completions_when_responses_text_is_empty(self):
        responses_response = SimpleNamespace(
            output_text="",
            output=[],
            usage=SimpleNamespace(input_tokens=6, output_tokens=3, total_tokens=9),
        )
        chat_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Fallback text."))],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, total_tokens=6),
        )

        with patch("main.llm.client.responses.create", return_value=responses_response) as responses_mock:
            with patch("main.llm.client.chat.completions.create", return_value=chat_response) as chat_mock:
                result = call_llm(
                    prompt="Test prompt",
                    model_name="gpt-5-mini",
                    params={"max_tokens": 64},
                )

        self.assertEqual(responses_mock.call_count, 1)
        self.assertEqual(chat_mock.call_count, 1)
        self.assertEqual(result.text, "Fallback text.")
        self.assertEqual(result.usage, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})


class HomePageTests(TestCase):
    def test_home_page_displays_updates_board(self):
        HomeUpdate.objects.create(
            date="2026-03-20",
            title="Targeted scene regeneration",
            body="Added !{...}! markers and post-regenerate highlight support.",
        )

        resp = self.client.get(reverse("home"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Updates")
        self.assertContains(resp, "2026-03-20")
        self.assertContains(resp, "Targeted scene regeneration")
        self.assertContains(resp, "Added !{...}! markers and post-regenerate highlight support.")


class HomeUpdateCreateViewTests(TestCase):
    def setUp(self):
        self.superuser = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password123",
        )
        self.regular_user = get_user_model().objects.create_user(
            username="writer",
            email="writer@example.com",
            password="password123",
        )

    def test_superuser_can_open_create_page_and_post_update(self):
        self.client.force_login(self.superuser)

        resp = self.client.post(
            reverse("home-update-create"),
            data={
                "title": "Added AI model selector",
                "date": "2026-03-20",
                "body": "Users can now switch between text generation models from the token usage page.",
            },
        )

        self.assertEqual(resp.status_code, 302)
        update = HomeUpdate.objects.get(title="Added AI model selector")
        self.assertEqual(update.body, "Users can now switch between text generation models from the token usage page.")

    def test_superuser_create_page_shows_title_date_and_body_text_fields(self):
        self.client.force_login(self.superuser)

        resp = self.client.get(reverse("home-update-create"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="title"', html=False)
        self.assertContains(resp, 'name="date"', html=False)
        self.assertContains(resp, 'name="body"', html=False)
        self.assertContains(resp, "Generate with AI")
        self.assertContains(resp, "AI can generate this from the body text")
        self.assertContains(resp, "Paste raw git or technical change notes here, then use Generate with AI.")

    def test_regular_user_cannot_open_create_page(self):
        self.client.force_login(self.regular_user)

        resp = self.client.get(reverse("home-update-create"))

        self.assertEqual(resp.status_code, 403)

    def test_superuser_can_generate_title_and_body_from_git_text(self):
        self.client.force_login(self.superuser)

        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text='{"title":"Improved update composer","body":"The update composer now turns pasted git notes into a cleaner user-facing summary and short title."}',
                usage={"ok": True},
            ),
        ) as mock_call:
            resp = self.client.post(
                reverse("home-update-regenerate"),
                data={
                    "body": "rework update composer to turn git text into user friendly explanation and summarized title",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "ok": True,
                "title": "Improved update composer",
                "body": "The update composer now turns pasted git notes into a cleaner user-facing summary and short title.",
            },
        )
        prompt = mock_call.call_args.kwargs["prompt"]
        self.assertIn("turn raw git or technical change notes into a short user-facing update", prompt)
        self.assertIn("rework update composer to turn git text into user friendly explanation and summarized title", prompt)
        self.assertIn('Return STRICT JSON only in the form: {"title":"...","body":"..."}', prompt)
        self.assertIn("Never answer with placeholder words", prompt)

    def test_superuser_generate_home_update_accepts_body_only_model_response(self):
        self.client.force_login(self.superuser)

        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text="Users can now switch between text generation models from the token usage page, and the active model is shown in the navbar for quick reference.",
                usage={"ok": True},
            ),
        ):
            resp = self.client.post(
                reverse("home-update-regenerate"),
                data={
                    "body": "add model selector to token usage page and show active model in navbar",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "ok": True,
                "title": "Added AI model selector",
                "body": "Users can now switch between text generation models from the token usage page, and the active model is shown in the navbar for quick reference.",
            },
        )

    def test_superuser_generate_home_update_falls_back_when_model_output_is_unusable(self):
        self.client.force_login(self.superuser)

        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text='{"title":"Rework the Post Update composer to auto-generate and preview titles from the body","body":"Rework the Post Update composer to auto-generate and preview titles from the body."}',
                usage={"ok": True},
            ),
        ):
            resp = self.client.post(
                reverse("home-update-regenerate"),
                data={
                    "body": "rework the Post Update composer to auto-generate and preview titles from the body",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "ok": True,
                "title": "Reworked Post Update composer",
                "body": "Reworked Post Update composer to improve clarity and day-to-day usability.",
                "warning": "Model returned unusable output; used fallback generation.",
            },
        )

    def test_superuser_generate_home_update_falls_back_when_model_fails(self):
        self.client.force_login(self.superuser)

        with patch("main.views.call_llm", side_effect=ValueError("Model response was empty.")):
            resp = self.client.post(
                reverse("home-update-regenerate"),
                data={
                    "body": "fix dashboard overflow",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "ok": True,
                "title": "Fixed dashboard overflow",
                "body": "Fixed issues around dashboard overflow so the workflow behaves more reliably.",
                "warning": "Model response was empty.",
            },
        )

    def test_regular_user_cannot_regenerate_home_update_copy(self):
        self.client.force_login(self.regular_user)

        resp = self.client.post(
            reverse("home-update-regenerate"),
            data={"body": "technical commit text"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(resp.status_code, 403)


class MoveSceneTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.project = NovelProject.objects.create(
            title="Test Project",
            slug="test-project",
            target_word_count=1000,
            owner=self.user,
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


class SceneStructurizeRenderTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.project = NovelProject.objects.create(
            title="Test Project",
            slug="test-project",
            target_word_count=1000,
            owner=self.user,
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

    def test_structurize_includes_selected_character_details_in_prompt(self):
        selected = Character.objects.create(
            project=self.project,
            name="Ava",
            role="Protagonist",
            age=22,
            gender="Female",
            personality="Driven and guarded.",
            appearance="Tall, watchful, practical.",
            background="Raised in cargo fleets.",
            goals="Expose the conspiracy.",
            voice_notes="Clipped, precise sentences.",
            description="Keeps emotional distance until pressured.",
            extra_fields={"secret": "Smuggling evidence in her jacket lining"},
        )
        Character.objects.create(
            project=self.project,
            name="Zed",
            role="Rival",
            personality="Provocative.",
        )

        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        with patch("main.views.call_llm", return_value=LLMResult(text="Draft text.", usage={"ok": True})) as mock_call:
            resp = self.client.post(
                url,
                data={
                    "order": 1,
                    "title": self.scene.title,
                    "summary": self.scene.summary,
                    "pov": self.scene.pov,
                    "location": self.scene.location,
                    "characters": [str(selected.id)],
                    "action": "structurize",
                },
            )

        self.assertEqual(resp.status_code, 302)
        prompt = mock_call.call_args.kwargs["prompt"]
        self.assertIn("Selected scene characters:", prompt)
        self.assertIn("- Ava: role=Protagonist; age=22; gender=Female", prompt)
        self.assertIn("personality=Driven and guarded.", prompt)
        self.assertIn("appearance=Tall, watchful, practical.", prompt)
        self.assertIn("background=Raised in cargo fleets.", prompt)
        self.assertIn("goals=Expose the conspiracy.", prompt)
        self.assertIn("voice_notes=Clipped, precise sentences.", prompt)
        self.assertIn("description=Keeps emotional distance until pressured.", prompt)
        self.assertIn("secret=Smuggling evidence in her jacket lining", prompt)
        self.assertNotIn("- Zed:", prompt)

    def test_structurize_includes_previous_scene_from_same_chapter_only(self):
        previous_scene = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter,
            order=1,
            title="Scene 0",
            summary="A quiet argument reveals the central lie.",
            pov="Mira",
            location="Observation deck",
            rendered_text="Mira corners Ava on the observation deck and forces the first crack in the cover story.",
        )
        self.scene.order = 2
        self.scene.save(update_fields=["order"])

        other_chapter = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.CHAPTER,
            parent=self.act,
            order=2,
            title="Chapter 2",
        )
        OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=other_chapter,
            order=1,
            title="Other chapter scene",
            summary="Should not leak into the prompt.",
        )

        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        with patch("main.views.call_llm", return_value=LLMResult(text="Draft text.", usage={"ok": True})) as mock_call:
            resp = self.client.post(
                url,
                data={
                    "order": 2,
                    "title": self.scene.title,
                    "summary": self.scene.summary,
                    "pov": self.scene.pov,
                    "location": self.scene.location,
                    "action": "structurize",
                },
            )

        self.assertEqual(resp.status_code, 302)
        prompt = mock_call.call_args.kwargs["prompt"]
        self.assertIn("Previous scene in this chapter:", prompt)
        self.assertIn("Title: Scene 0", prompt)
        self.assertIn("POV: Mira", prompt)
        self.assertIn("Location: Observation deck", prompt)
        self.assertIn("Summary: A quiet argument reveals the central lie.", prompt)
        self.assertIn("Text for continuity: Mira corners Ava on the observation deck", prompt)
        self.assertNotIn("Other chapter scene", prompt)
        self.assertNotIn("Should not leak into the prompt.", prompt)

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

    def test_regenerate_targeted_text_uses_full_draft_context(self):
        self.scene.structure_json = "Opening beat. !{Old line.}! Closing beat."
        self.scene.save(update_fields=["structure_json"])

        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(text='{"segments": ["New line."]}', usage={"ok": True}),
        ) as mock_call:
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
                    "action": "reshuffle",
                },
            )

        self.assertEqual(resp.status_code, 302)
        self.scene.refresh_from_db()
        self.assertEqual(self.scene.structure_json, "Opening beat. New line. Closing beat.")
        self.assertIn("hl=", resp["Location"])
        prompt = mock_call.call_args.kwargs["prompt"]
        self.assertIn("Full draft with marked target sections:", prompt)
        self.assertIn("Opening beat. !{Old line.}! Closing beat.", prompt)

    def test_edit_scene_shows_regenerate_marker_buttons(self):
        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="draft-target-btn"')
        self.assertContains(resp, "!{...}!")
        self.assertContains(resp, 'id="draft-unbrace-btn"')
        self.assertContains(resp, "Remove {}")


class SceneLocationDropdownTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.project = NovelProject.objects.create(
            title="Test Project",
            slug="test-project",
            target_word_count=1000,
            owner=self.user,
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
        )
        self.scene = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter,
            order=1,
            title="Scene 1",
            summary="",
            location="Docking Bay",
        )
        self.root_location = Location.objects.create(project=self.project, name="Ship", description="", is_root=True)
        Location.objects.create(project=self.project, parent=self.root_location, name="Docking Bay", description="")
        Location.objects.create(project=self.project, parent=self.root_location, name="Garden", description="")

    def test_edit_scene_renders_location_select_with_create_option(self):
        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="location"')
        self.assertContains(resp, 'value="Docking Bay"')
        self.assertContains(resp, 'value="Garden"')
        self.assertContains(resp, 'value="__create__"')

    def test_posting_create_sentinel_redirects_to_location_creator(self):
        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        resp = self.client.post(
            url,
            data={
                "order": 1,
                "title": "Scene 1",
                "summary": "",
                "pov": "",
                "location": "__create__",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("location-create", kwargs={"slug": self.project.slug}), resp["Location"])
        self.assertIn("next=", resp["Location"])

    def test_location_create_with_next_returns_to_scene_with_prefill(self):
        scene_url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        create_url = reverse("location-create", kwargs={"slug": self.project.slug}) + "?next=" + quote(scene_url, safe="")
        resp = self.client.post(
            create_url,
            data={"parent": str(self.root_location.id), "name": "Engine Room", "description": ""},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith(scene_url))
        self.assertIn("prefill_location=Engine+Room", resp["Location"])


class CharacterViewsTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.project_a = NovelProject.objects.create(
            title="Project A",
            slug="project-a",
            target_word_count=1000,
            owner=self.user,
        )
        self.project_b = NovelProject.objects.create(
            title="Project B",
            slug="project-b",
            target_word_count=1000,
            owner=self.user,
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

    def test_add_details_strips_repeated_prefix_from_existing_field(self):
        url = reverse("character-add-details", kwargs={"slug": self.project_a.slug})
        existing = "tall and lean, with rugged features; short-cropped dark hair and deep-set blue eyes"
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text=json.dumps(
                    {
                        "appearance": (
                            existing
                            + "; often wears practical, worn work attire that reflects his hands-on job"
                        )
                    }
                ),
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
                    "personality": "",
                    "appearance": existing,
                    "background": "",
                    "goals": "",
                    "voice_notes": "",
                    "description": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "ok": True,
                "suggestions": {
                    "appearance": "often wears practical, worn work attire that reflects his hands-on job"
                },
            },
        )


class ProjectSharedAccessTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.other_user = get_user_model().objects.create_user(
            username="project-owner",
            email="project-owner@example.com",
            password="password123",
        )
        self.project = NovelProject.objects.create(
            title="Shared Project",
            slug="shared-project",
            target_word_count=1000,
            owner=self.other_user,
        )

    def test_project_list_shows_projects_owned_by_other_users(self):
        url = reverse("project-list")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Shared Project")

    def test_project_detail_allows_shared_access(self):
        url = reverse("project-detail", kwargs={"slug": self.project.slug})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Shared Project")


class FullNovelViewTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.project = NovelProject.objects.create(
            title="Full Novel Project",
            slug="full-novel-project",
            target_word_count=1000,
            owner=self.user,
        )
        self.act = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.ACT,
            parent=None,
            order=1,
            title="Act I",
        )
        self.chapter_one = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.CHAPTER,
            parent=self.act,
            order=1,
            title="Arrival at Blackwater",
        )
        self.chapter_two = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.CHAPTER,
            parent=self.act,
            order=2,
            title="The Terms of Escape",
        )
        OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter_one,
            order=1,
            title="Scene 1",
            rendered_text="First chapter opening.",
        )
        OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter_one,
            order=2,
            title="Scene 2",
            rendered_text="First chapter closing.",
        )
        OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter_two,
            order=1,
            title="Scene 3",
            rendered_text="Second chapter opening.",
        )
        OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter_two,
            order=2,
            title="Scene 4",
            rendered_text="",
        )

    def test_full_novel_view_groups_rendered_text_under_chapter_titles(self):
        resp = self.client.get(reverse("full-novel", kwargs={"slug": self.project.slug}))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.context["outline_tree"],
            [
                {
                    "act": {
                        "title": "Act I",
                        "anchor": f"act-{self.act.id}",
                    },
                    "chapters": [
                        {
                            "title": "Arrival at Blackwater",
                            "anchor": f"chapter-{self.chapter_one.id}",
                            "scenes": [
                                {
                                    "title": "Scene 1",
                                    "anchor": resp.context["outline_tree"][0]["chapters"][0]["scenes"][0]["anchor"],
                                    "pov": "",
                                    "location": "",
                                },
                                {
                                    "title": "Scene 2",
                                    "anchor": resp.context["outline_tree"][0]["chapters"][0]["scenes"][1]["anchor"],
                                    "pov": "",
                                    "location": "",
                                },
                            ],
                        },
                        {
                            "title": "The Terms of Escape",
                            "anchor": f"chapter-{self.chapter_two.id}",
                            "scenes": [
                                {
                                    "title": "Scene 3",
                                    "anchor": resp.context["outline_tree"][0]["chapters"][1]["scenes"][0]["anchor"],
                                    "pov": "",
                                    "location": "",
                                },
                                {
                                    "title": "Scene 4",
                                    "anchor": "",
                                    "pov": "",
                                    "location": "",
                                },
                            ],
                        },
                    ],
                }
            ],
        )
        self.assertEqual(
            resp.context["chapter_sections"],
            [
                {
                    "title": "Arrival at Blackwater",
                    "anchor": f"chapter-{self.chapter_one.id}",
                    "text": "First chapter opening.\n\nFirst chapter closing.",
                },
                {
                    "title": "The Terms of Escape",
                    "anchor": f"chapter-{self.chapter_two.id}",
                    "text": "Second chapter opening.",
                },
            ],
        )
        self.assertContains(resp, "Table of contents")
        self.assertContains(resp, "Act I")
        self.assertContains(resp, 'href="#act-', html=False)
        self.assertContains(resp, f'href="#chapter-{self.chapter_one.id}"', html=False)
        self.assertContains(resp, f'href="#chapter-{self.chapter_two.id}"', html=False)
        self.assertContains(resp, "Scene 1")
        self.assertContains(resp, "Scene 2")
        self.assertContains(resp, "Scene 3")
        self.assertContains(resp, "Scene 4")
        self.assertContains(resp, f'id="act-{self.act.id}"', html=False)
        self.assertContains(resp, f'id="chapter-{self.chapter_one.id}"', html=False)
        self.assertContains(resp, f'id="chapter-{self.chapter_two.id}"', html=False)
        self.assertContains(resp, 'id="scene-', html=False)
        self.assertContains(resp, "Arrival at Blackwater")
        self.assertContains(resp, "The Terms of Escape")
        self.assertContains(resp, "First chapter opening.")
        self.assertContains(resp, "First chapter closing.")
        self.assertContains(resp, "Second chapter opening.")


class ProjectArchiveTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.active_project = NovelProject.objects.create(
            title="Active Project",
            slug="active-project",
            target_word_count=1000,
            owner=self.user,
        )
        self.archived_project = NovelProject.objects.create(
            title="Archived Project",
            slug="archived-project",
            target_word_count=2000,
            owner=self.user,
            is_archived=True,
        )

    def test_project_list_excludes_archived_projects(self):
        resp = self.client.get(reverse("project-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Active Project")
        self.assertNotContains(resp, "Archived Project")
        self.assertContains(resp, "Archive")

    def test_archive_page_shows_only_archived_projects(self):
        resp = self.client.get(reverse("project-archive-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Archived Project")
        self.assertNotContains(resp, "Active Project")
        self.assertContains(resp, "Restore")

    def test_archive_project_marks_project_as_archived(self):
        resp = self.client.post(
            reverse("project-archive", kwargs={"slug": self.active_project.slug}),
            data={"next": reverse("project-list")},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("project-list"))
        self.active_project.refresh_from_db()
        self.assertTrue(self.active_project.is_archived)

    def test_restore_project_marks_project_as_active(self):
        resp = self.client.post(
            reverse("project-restore", kwargs={"slug": self.archived_project.slug}),
            data={"next": reverse("project-archive-list")},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("project-archive-list"))
        self.archived_project.refresh_from_db()
        self.assertFalse(self.archived_project.is_archived)


class TokenUsageViewTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.project_a = NovelProject.objects.create(
            title="Project A",
            slug="project-a",
            target_word_count=1000,
            owner=self.user,
        )
        self.project_b = NovelProject.objects.create(
            title="Project B",
            slug="project-b",
            target_word_count=1000,
            owner=self.user,
        )

    def _create_run(self, *, project, run_type, created_at, usage):
        run = GenerationRun.objects.create(
            project=project,
            run_type=run_type,
            status=GenerationRun.Status.SUCCEEDED,
            usage=usage,
        )
        GenerationRun.objects.filter(pk=run.pk).update(created_at=created_at, updated_at=created_at)
        return GenerationRun.objects.get(pk=run.pk)

    def test_token_usage_view_groups_daily_totals_and_project_totals(self):
        self._create_run(
            project=self.project_a,
            run_type=GenerationRun.RunType.BIBLE,
            created_at=timezone.make_aware(datetime(2026, 3, 24, 9, 0)),
            usage={"total_tokens": 120},
        )
        self._create_run(
            project=self.project_a,
            run_type=GenerationRun.RunType.SCENE,
            created_at=timezone.make_aware(datetime(2026, 3, 24, 10, 0)),
            usage={"prompt_tokens": 30, "completion_tokens": 45},
        )
        self._create_run(
            project=self.project_b,
            run_type=GenerationRun.RunType.BIBLE,
            created_at=timezone.make_aware(datetime(2026, 3, 25, 11, 30)),
            usage={"total_tokens": 200},
        )
        self._create_run(
            project=self.project_b,
            run_type=GenerationRun.RunType.OUTLINE,
            created_at=timezone.make_aware(datetime(2026, 3, 25, 13, 15)),
            usage={"generator": "local-template"},
        )

        resp = self.client.get(reverse("token-usage"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Token usage")
        self.assertContains(resp, "395")
        self.assertContains(resp, "Generate Bible")
        self.assertContains(resp, "Generate All Scenes")
        self.assertContains(resp, "Project A")
        self.assertContains(resp, "Project B")
        self.assertContains(resp, "195")
        self.assertContains(resp, "200")

    def test_project_brainstorm_records_token_usage_for_report(self):
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text='{"genre": "Speculative mystery"}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ):
            resp = self.client.post(
                reverse("project-brainstorm", kwargs={"slug": self.project_a.slug}),
                data={
                    "seed_idea": "",
                    "genre": "",
                    "tone": "",
                    "style_notes": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        usage_resp = self.client.get(reverse("token-usage"))
        self.assertEqual(usage_resp.status_code, 200)
        self.assertContains(usage_resp, "Project Brainstorm")
        self.assertContains(usage_resp, "55")
        self.assertContains(usage_resp, "Project A")

    def test_token_usage_page_saves_per_user_text_model_selection(self):
        resp = self.client.post(
            reverse("token-usage"),
            data={"text_model_name": "gpt-5-mini"},
            follow=True,
        )

        self.assertEqual(resp.status_code, 200)
        preference = UserTextModelPreference.objects.get(user=self.user)
        self.assertEqual(preference.text_model_name, "gpt-5-mini")
        self.assertContains(resp, "Current selection:")
        self.assertContains(resp, "gpt-5-mini")

    def test_project_brainstorm_uses_selected_user_text_model(self):
        UserTextModelPreference.objects.create(user=self.user, text_model_name="gpt-5-mini")

        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text='{"genre": "Speculative mystery"}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ) as mock_call:
            resp = self.client.post(
                reverse("project-brainstorm", kwargs={"slug": self.project_a.slug}),
                data={
                    "seed_idea": "",
                    "genre": "",
                    "tone": "",
                    "style_notes": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_call.call_args.kwargs["model_name"], "gpt-5-mini")

    def test_navbar_shows_active_text_model_badge(self):
        UserTextModelPreference.objects.create(user=self.user, text_model_name="gpt-5-mini")

        resp = self.client.get(reverse("project-list"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Model")
        self.assertContains(resp, "gpt-5-mini")


class LocationViewsTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.other_user = get_user_model().objects.create_user(
            username="other",
            email="other@example.com",
            password="password123",
        )
        self.project_a = NovelProject.objects.create(title="Project A", slug="project-a", target_word_count=1000, owner=self.user)
        self.project_b = NovelProject.objects.create(title="Project B", slug="project-b", target_word_count=1000, owner=self.other_user)
        self.root_a = Location.objects.create(project=self.project_a, name="Ship", objects_map={}, is_root=True)
        self.loc_a = Location.objects.create(
            project=self.project_a,
            parent=self.root_a,
            name="Docking Bay",
            objects_map={"crate": "sealed"},
        )
        self.root_b = Location.objects.create(project=self.project_b, name="Estate", objects_map={}, is_root=True)
        self.loc_b = Location.objects.create(project=self.project_b, parent=self.root_b, name="Garden", objects_map={})

    def test_list_scoped_to_project(self):
        url = reverse("location-list", kwargs={"slug": self.project_a.slug})
        resp = self.client.get(url)
        self.assertContains(resp, "Docking Bay")
        self.assertNotContains(resp, "Garden")

    def test_shared_access_allows_opening_other_users_location_pages(self):
        url = reverse("location-world-map", kwargs={"slug": self.project_b.slug})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Garden")

    def test_create_parses_object_pairs(self):
        url = reverse("location-create", kwargs={"slug": self.project_a.slug})
        resp = self.client.post(
            url,
            data={
                "parent": str(self.root_a.id),
                "name": "Market",
                "description": "Busy and loud.",
                "object_key": ["stall", "lamp"],
                "object_value": ["fruit vendor", "flickering neon"],
            },
        )
        self.assertEqual(resp.status_code, 302)
        loc = Location.objects.get(project=self.project_a, name="Market")
        self.assertEqual(loc.parent_id, self.root_a.id)
        self.assertEqual(loc.objects_map, {"stall": "fruit vendor", "lamp": "flickering neon"})

    def test_create_form_defaults_parent_to_root(self):
        url = reverse("location-create", kwargs={"slug": self.project_a.slug})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'value="{self.root_a.id}" selected')

    def test_create_defaults_to_root_when_parent_is_not_selected(self):
        url = reverse("location-create", kwargs={"slug": self.project_a.slug})
        resp = self.client.post(
            url,
            data={
                "name": "Market",
                "description": "Busy and loud.",
                "object_key": [],
                "object_value": [],
            },
        )
        self.assertEqual(resp.status_code, 302)
        loc = Location.objects.get(project=self.project_a, name="Market")
        self.assertEqual(loc.parent_id, self.root_a.id)

    def test_list_renders_nested_path(self):
        url = reverse("location-list", kwargs={"slug": self.project_a.slug})
        resp = self.client.get(url)
        self.assertContains(resp, "World map")
        self.assertContains(resp, "Ship / Docking Bay")

    def test_world_map_page_renders_location_boxes(self):
        url = reverse("location-world-map", kwargs={"slug": self.project_a.slug})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "World Map")
        self.assertContains(resp, "class=\"world-location-box")
        self.assertContains(resp, "class=\"world-location-children")
        self.assertContains(resp, "data-location-move-url")
        self.assertContains(resp, "Docking Bay")
        self.assertNotContains(resp, "Garden")

    def test_move_location_reparents_under_new_parent(self):
        market = Location.objects.create(project=self.project_a, parent=self.root_a, name="Market", objects_map={})
        url = reverse("location-move", kwargs={"slug": self.project_a.slug})
        resp = self.client.post(
            url,
            data={
                "location_id": str(market.id),
                "target_parent_id": str(self.loc_a.id),
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True})
        market.refresh_from_db()
        self.assertEqual(market.parent_id, self.loc_a.id)

    def test_move_location_rejects_moving_root(self):
        url = reverse("location-move", kwargs={"slug": self.project_a.slug})
        resp = self.client.post(
            url,
            data={
                "location_id": str(self.root_a.id),
                "target_parent_id": str(self.loc_a.id),
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["ok"], False)

    def test_root_cannot_be_deleted(self):
        url = reverse("location-delete", kwargs={"slug": self.project_a.slug, "pk": self.root_a.id})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Location.objects.filter(id=self.root_a.id).exists())

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
