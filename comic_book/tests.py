import json

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from unittest.mock import patch

from .forms import (
    ComicBibleForm,
    ComicPanelNodeForm,
    ComicCharacterForm,
    ComicIssueForm,
    ComicLocationForm,
    ComicPageForm,
    ComicPanelForm,
    ComicProjectForm,
)
from main.llm import LLMResult
from .models import ComicBible, ComicPanelNode, ComicCharacter, ComicIssue, ComicLocation, ComicPage, ComicPanel, ComicProject


class ComicBookAppTests(TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="panelmaker",
            email="panelmaker@example.com",
            password="password123",
        )

    def _create_project(self, *, owner=None, slug="star-signal", title="Star Signal") -> ComicProject:
        return ComicProject.objects.create(
            owner=owner or self.user,
            title=title,
            slug=slug,
            logline="A courier smuggles a cosmic message through a collapsing empire.",
            genre="Sci-fi",
            tone="High tension",
            target_audience="Teen+",
        )

    def test_comic_book_page_requires_authentication(self):
        response = self.client.get(reverse("comic_book:index"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_comic_page_edit_requires_authentication_before_project_lookup(self):
        project = self._create_project(slug="shahed")
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening")

        response = self.client.get(
            reverse(
                "comic_book:page-edit",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "pk": page.pk},
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_authenticated_user_can_open_project_list(self):
        project = self._create_project()
        self.client.force_login(self.user)

        response = self.client.get(reverse("comic_book:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Comic book creator")
        self.assertContains(response, project.title)
        self.assertContains(response, "New comic project")

    def test_authenticated_homepage_shows_comic_book_entry_points(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/comic-book/"', html=False)
        self.assertContains(response, "Open Comic Book")

    def test_non_superuser_does_not_see_comic_book_navbar_link(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<a href="/comic-book/">Comic Book</a>', html=False)

    def test_superuser_sees_comic_book_navbar_link(self):
        admin_user = get_user_model().objects.create_superuser(
            username="adminuser",
            email="admin@example.com",
            password="password123",
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<summary>SuperUsers</summary>", html=False)
        self.assertContains(response, '<a href="/comic-book/">Comic Book</a>', html=False)

    def test_other_users_cannot_open_project_dashboard(self):
        other_user = get_user_model().objects.create_user(
            username="other",
            email="other@example.com",
            password="password123",
        )
        project = self._create_project(owner=other_user, slug="hidden-station", title="Hidden Station")
        self.client.force_login(self.user)

        response = self.client.get(reverse("comic_book:project-dashboard", kwargs={"slug": project.slug}))

        self.assertEqual(response.status_code, 404)

    def test_issue_creation_seeds_pages_from_planned_page_count(self):
        project = self._create_project()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("comic_book:issue-create", kwargs={"slug": project.slug}),
            data={
                "number": 1,
                "title": "Issue One",
                "summary": "The courier receives the signal and triggers a chase.",
                "theme": "Trust",
                "status": ComicIssue.Status.PLANNING,
                "planned_page_count": 4,
                "opening_hook": "A warning arrives inside a dead satellite.",
                "closing_hook": "The courier opens the vault on the final page.",
                "notes": "Keep the pacing sharp.",
            },
        )

        self.assertEqual(response.status_code, 302)
        issue = ComicIssue.objects.get(project=project, number=1)
        self.assertEqual(issue.pages.count(), 4)
        self.assertEqual(
            list(issue.pages.order_by("page_number").values_list("page_number", flat=True)),
            [1, 2, 3, 4],
        )

    def test_issue_create_page_renders_brainstorm_and_add_detail_controls(self):
        project = self._create_project()
        self.client.force_login(self.user)

        response = self.client.get(reverse("comic_book:issue-create", kwargs={"slug": project.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="comic-issue-brainstorm-btn"', html=False)
        self.assertContains(response, 'id="comic-issue-add-details-btn"', html=False)
        self.assertContains(response, reverse("comic_book:issue-brainstorm", kwargs={"slug": project.slug}))
        self.assertContains(response, reverse("comic_book:issue-add-details", kwargs={"slug": project.slug}))

    def test_issue_brainstorm_returns_only_empty_field_suggestions(self):
        project = self._create_project()
        self.client.force_login(self.user)

        with patch(
            "comic_book.views.call_llm",
            return_value=LLMResult(
                text='{"summary":"A courier chases a dead transmission across a quarantined city.","theme":"Trust under pressure","opening_hook":"A dead satellite wakes up and speaks Nika\'s name.","notes":"Lean into rain, neon, and failed surveillance."}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ) as mock_call:
            response = self.client.post(
                reverse("comic_book:issue-brainstorm", kwargs={"slug": project.slug}),
                data={
                    "number": 1,
                    "title": "Issue One",
                    "summary": "",
                    "theme": "",
                    "status": ComicIssue.Status.PLANNING,
                    "planned_page_count": 22,
                    "opening_hook": "",
                    "closing_hook": "",
                    "notes": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "suggestions": {
                    "summary": "A courier chases a dead transmission across a quarantined city.",
                    "theme": "Trust under pressure",
                    "opening_hook": "A dead satellite wakes up and speaks Nika's name.",
                    "notes": "Lean into rain, neon, and failed surveillance.",
                },
            },
        )
        self.assertIn("Project title: Star Signal", mock_call.call_args.kwargs["prompt"])

    def test_issue_add_details_trims_overlapping_rewrite_to_prevent_duplication(self):
        project = self._create_project()
        self.client.force_login(self.user)

        with patch(
            "comic_book.views.call_llm",
            return_value=LLMResult(
                text='{"summary":"The courier receives the signal and triggers a chase. Imperial drones lock down the district."}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ):
            response = self.client.post(
                reverse("comic_book:issue-add-details", kwargs={"slug": project.slug}),
                data={
                    "number": 1,
                    "title": "Issue One",
                    "summary": "The courier receives the signal and triggers a chase.",
                    "theme": "Trust",
                    "status": ComicIssue.Status.PLANNING,
                    "planned_page_count": 22,
                    "opening_hook": "",
                    "closing_hook": "",
                    "notes": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"ok": True, "suggestions": {"summary": "Imperial drones lock down the district."}},
        )

    def test_bible_edit_page_renders_brainstorm_and_add_detail_controls(self):
        project = self._create_project()
        self.client.force_login(self.user)

        response = self.client.get(reverse("comic_book:bible-edit", kwargs={"slug": project.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="comic-bible-brainstorm-btn"', html=False)
        self.assertContains(response, 'id="comic-bible-add-details-btn"', html=False)
        self.assertContains(response, reverse("comic_book:bible-brainstorm", kwargs={"slug": project.slug}))
        self.assertContains(response, reverse("comic_book:bible-add-details", kwargs={"slug": project.slug}))

    def test_bible_brainstorm_returns_only_empty_field_suggestions(self):
        project = self._create_project()
        ComicBible.objects.create(
            project=project,
            continuity_rules="Signals cannot be decoded without a living relay pilot.",
        )
        ComicCharacter.objects.create(project=project, name="Nika Vale", role="Courier")
        ComicLocation.objects.create(project=project, name="Relay Port", description="A decaying orbital dock.")
        self.client.force_login(self.user)

        with patch(
            "comic_book.views.call_llm",
            return_value=LLMResult(
                text='{"premise":"A courier carries a forbidden signal through a collapsing empire while rival factions race to control what it reveals.","visual_rules":"Use hard-edged sci-fi silhouettes, signal glow, and dense industrial decay.","cast_notes":"Keep the core cast small and tension-driven so every interaction shifts trust."}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ) as mock_call:
            response = self.client.post(
                reverse("comic_book:bible-brainstorm", kwargs={"slug": project.slug}),
                data={
                    "premise": "",
                    "world_rules": "FTL travel is unstable and expensive.",
                    "visual_rules": "",
                    "continuity_rules": "",
                    "cast_notes": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "suggestions": {
                    "premise": "A courier carries a forbidden signal through a collapsing empire while rival factions race to control what it reveals.",
                    "visual_rules": "Use hard-edged sci-fi silhouettes, signal glow, and dense industrial decay.",
                    "cast_notes": "Keep the core cast small and tension-driven so every interaction shifts trust.",
                },
            },
        )
        self.assertIn("Project title: Star Signal", mock_call.call_args.kwargs["prompt"])
        self.assertIn("Saved series bible context:", mock_call.call_args.kwargs["prompt"])
        self.assertIn(
            "Comic bible continuity rules: Signals cannot be decoded without a living relay pilot.",
            mock_call.call_args.kwargs["prompt"],
        )
        self.assertIn("Key characters:", mock_call.call_args.kwargs["prompt"])
        self.assertIn("Key locations:", mock_call.call_args.kwargs["prompt"])

    def test_bible_add_details_trims_overlapping_rewrite_to_prevent_duplication(self):
        project = self._create_project()
        ComicBible.objects.create(
            project=project,
            premise="A courier carries a forbidden signal through a collapsing empire.",
        )
        self.client.force_login(self.user)

        with patch(
            "comic_book.views.call_llm",
            return_value=LLMResult(
                text='{"premise":"A courier carries a forbidden signal through a collapsing empire. Rival factions begin hunting the message before it can be decoded."}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ) as mock_call:
            response = self.client.post(
                reverse("comic_book:bible-add-details", kwargs={"slug": project.slug}),
                data={
                    "premise": "A courier carries a forbidden signal through a collapsing empire.",
                    "world_rules": "",
                    "visual_rules": "",
                    "continuity_rules": "",
                    "cast_notes": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"ok": True, "suggestions": {"premise": "Rival factions begin hunting the message before it can be decoded."}},
        )
        self.assertIn("Saved series bible context:", mock_call.call_args.kwargs["prompt"])
        self.assertIn(
            "Comic bible premise: A courier carries a forbidden signal through a collapsing empire.",
            mock_call.call_args.kwargs["prompt"],
        )

    def test_character_create_page_renders_brainstorm_and_add_detail_controls(self):
        project = self._create_project()
        self.client.force_login(self.user)

        response = self.client.get(reverse("comic_book:character-create", kwargs={"slug": project.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="comic-character-brainstorm-btn"', html=False)
        self.assertContains(response, 'id="comic-character-add-details-btn"', html=False)
        self.assertContains(response, 'id="comic-character-faces-btn"', html=False)
        self.assertContains(response, 'id="comic-character-full-body-btn"', html=False)
        self.assertContains(response, 'id="comic-character-face-frontal-upload"', html=False)
        self.assertContains(response, 'id="comic-character-face-sideways-upload"', html=False)
        self.assertContains(response, 'id="comic-character-full-body-upload"', html=False)
        self.assertContains(response, reverse("comic_book:character-brainstorm", kwargs={"slug": project.slug}))
        self.assertContains(response, reverse("comic_book:character-add-details", kwargs={"slug": project.slug}))
        self.assertContains(response, reverse("comic_book:character-faces-preview", kwargs={"slug": project.slug}))
        self.assertContains(response, reverse("comic_book:character-full-body-preview", kwargs={"slug": project.slug}))
        self.assertContains(response, 'name="age"', html=False)
        self.assertContains(response, 'name="gender"', html=False)

    def test_character_list_uses_frontal_face_image_for_hover_preview(self):
        project = self._create_project()
        ComicCharacter.objects.create(
            project=project,
            name="Sera Flint",
            role="Cipher thief",
            frontal_face_image_data_url="data:image/png;base64,frontal-saved",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("comic_book:character-list", kwargs={"slug": project.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="list-item comic-list-item character-card character-card-has-portrait"', html=False)
        self.assertContains(response, 'data-portrait-url="data:image/png;base64,frontal-saved"', html=False)
        self.assertContains(response, "main/character_portrait_hover")

    def test_character_edit_page_renders_face_generation_controls(self):
        project = self._create_project()
        character = ComicCharacter.objects.create(project=project, name="Sera Flint", role="Cipher thief")
        self.client.force_login(self.user)

        response = self.client.get(reverse("comic_book:character-edit", kwargs={"slug": project.slug, "pk": character.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="comic-character-faces-btn"', html=False)
        self.assertContains(response, 'id="comic-character-full-body-btn"', html=False)
        self.assertContains(
            response,
            reverse("comic_book:character-faces", kwargs={"slug": project.slug, "pk": character.pk}),
        )
        self.assertContains(
            response,
            reverse("comic_book:character-full-body", kwargs={"slug": project.slug, "pk": character.pk}),
        )
        self.assertContains(response, "Frontal face")
        self.assertContains(response, "Sideways face")
        self.assertContains(response, "Full body")
        self.assertContains(response, "portrait-frame portrait-frame-face")
        self.assertContains(response, "portrait-frame portrait-frame-full-body")
        self.assertContains(response, 'id="comic-character-face-frontal-download"', html=False)
        self.assertContains(response, 'id="comic-character-face-sideways-download"', html=False)
        self.assertContains(response, 'id="comic-character-full-body-download"', html=False)

    def test_character_edit_page_does_not_repost_saved_image_data(self):
        project = self._create_project()
        character = ComicCharacter.objects.create(
            project=project,
            name="Sera Flint",
            role="Cipher thief",
            frontal_face_image_data_url="data:image/png;base64,frontal-saved",
            sideways_face_image_data_url="data:image/png;base64,sideways-saved",
            full_body_image_data_url="data:image/png;base64,body-saved",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("comic_book:character-edit", kwargs={"slug": project.slug, "pk": character.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="comic-character-face-frontal-img"', html=False)
        self.assertContains(response, 'href="data:image/png;base64,frontal-saved"', html=False)
        self.assertContains(response, 'name="frontal_face_image_data_url" id="comic-character-face-frontal-input" value=""', html=False)
        self.assertContains(response, 'name="sideways_face_image_data_url" id="comic-character-face-sideways-input" value=""', html=False)
        self.assertContains(response, 'name="full_body_image_data_url" id="comic-character-full-body-input" value=""', html=False)

    def test_character_edit_save_stays_on_edit_page(self):
        project = self._create_project()
        character = ComicCharacter.objects.create(project=project, name="Sera Flint", role="Cipher thief")
        self.client.force_login(self.user)

        edit_url = reverse("comic_book:character-edit", kwargs={"slug": project.slug, "pk": character.pk})
        response = self.client.post(
            edit_url,
            data={
                "name": "Sera Flint",
                "role": "Cipher thief",
                "age": "29",
                "gender": "Woman",
                "description": "Lean, guarded, and always reading exits before people.",
                "costume_notes": "Dark utility jacket.",
                "visual_notes": "Shaved sidecut.",
                "voice_notes": "Dry sarcasm.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], edit_url)

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_preview_character_faces_returns_two_images_for_unsaved_character(self):
        project = self._create_project()
        self.client.force_login(self.user)

        prompts = []
        sizes = []

        def fake_generate_image_data_url(*, prompt, model_name, size):
            prompts.append(prompt)
            sizes.append(size)
            return f"data:image/png;base64,preview{len(prompts)}"

        with patch("comic_book.views.generate_image_data_url", side_effect=fake_generate_image_data_url):
            response = self.client.post(
                reverse("comic_book:character-faces-preview", kwargs={"slug": project.slug}),
                data={
                    "name": "Sera Flint",
                    "role": "Cipher thief",
                    "description": "Lean, guarded, and always reading exits before people.",
                    "costume_notes": "Dark utility jacket with signal-thread seams.",
                    "visual_notes": "Shaved sidecut, narrow face, tired eyes.",
                    "voice_notes": "Short tactical phrasing with dry sarcasm.",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "frontal_face_image_url": "data:image/png;base64,preview1",
                "sideways_face_image_url": "data:image/png;base64,preview2",
            },
        )
        self.assertEqual(len(prompts), 2)
        self.assertEqual(sizes, ["1024x1024", "1024x1024"])
        self.assertIn("Pose target: straight-on frontal face.", prompts[0])
        self.assertIn("Pose target: sideways profile face.", prompts[1])
        self.assertIn("complete head, hairline, chin, neck, and upper shoulders", prompts[0])
        self.assertIn("small safety gap above the hair", prompts[0])
        self.assertIn("no large blank padding, no cropped head", prompts[0])
        self.assertIn("complete head, hairline, nose, chin, neck, and upper shoulders", prompts[1])
        self.assertIn("small safety gap above the hair", prompts[1])

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_preview_character_full_body_returns_image_for_unsaved_character(self):
        project = self._create_project()
        self.client.force_login(self.user)

        prompts = []
        sizes = []

        def fake_generate_image_data_url(*, prompt, model_name, size):
            prompts.append(prompt)
            sizes.append(size)
            return "data:image/png;base64,bodypreview"

        with patch("comic_book.views.generate_image_data_url", side_effect=fake_generate_image_data_url):
            response = self.client.post(
                reverse("comic_book:character-full-body-preview", kwargs={"slug": project.slug}),
                data={
                    "name": "Sera Flint",
                    "role": "Cipher thief",
                    "description": "Lean, guarded, and always reading exits before people.",
                    "costume_notes": "Dark utility jacket with signal-thread seams.",
                    "visual_notes": "Shaved sidecut, narrow face, tired eyes.",
                    "voice_notes": "Short tactical phrasing with dry sarcasm.",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "full_body_image_url": "data:image/png;base64,bodypreview"})
        self.assertEqual(len(prompts), 1)
        self.assertEqual(sizes, ["1024x1536"])
        self.assertIn("Pose target: full-body frontal view.", prompts[0])
        self.assertIn("very top of the hair to the soles of the boots", prompts[0])
        self.assertIn("complete top of the head", prompts[0])

    def test_character_brainstorm_returns_only_empty_field_suggestions(self):
        project = self._create_project()
        ComicCharacter.objects.create(project=project, name="Nika Vale", role="Courier")
        self.client.force_login(self.user)

        with patch(
            "comic_book.views.call_llm",
            return_value=LLMResult(
                text='{"age":29,"gender":"Woman","description":"- A smuggler with a rigid code.\\n- Hides panic behind clipped competence.","visual_notes":"- Sharp undercut silhouette.\\n- Tired eyes.\\n- Always scanning exits.","voice_notes":"- Short, tactical sentences.\\n- Dry deflection under stress."}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ) as mock_call:
            response = self.client.post(
                reverse("comic_book:character-brainstorm", kwargs={"slug": project.slug}),
                data={
                    "name": "Sera Flint",
                    "role": "Cipher thief",
                    "age": "",
                    "gender": "",
                    "description": "",
                    "costume_notes": "",
                    "visual_notes": "",
                    "voice_notes": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "suggestions": {
                    "age": "29",
                    "gender": "Woman",
                    "description": "- A smuggler with a rigid code.\n- Hides panic behind clipped competence.",
                    "visual_notes": "- Sharp undercut silhouette.\n- Tired eyes.\n- Always scanning exits.",
                    "voice_notes": "- Short, tactical sentences.\n- Dry deflection under stress.",
                },
            },
        )
        self.assertIn("Project title: Star Signal", mock_call.call_args.kwargs["prompt"])
        self.assertIn("Key characters:", mock_call.call_args.kwargs["prompt"])

    def test_character_add_details_trims_overlapping_rewrite_to_prevent_duplication(self):
        project = self._create_project()
        self.client.force_login(self.user)

        with patch(
            "comic_book.views.call_llm",
            return_value=LLMResult(
                text='{"description":"- She keeps one hand near a stolen key at all times."}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ):
            response = self.client.post(
                reverse("comic_book:character-add-details", kwargs={"slug": project.slug}),
                data={
                    "name": "Sera Flint",
                    "role": "Cipher thief",
                    "age": "",
                    "gender": "",
                    "description": "A smuggler with a rigid code who hides panic behind clipped competence.",
                    "costume_notes": "",
                    "visual_notes": "",
                    "voice_notes": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"ok": True, "suggestions": {"description": "- She keeps one hand near a stolen key at all times."}},
        )

    def test_location_create_page_renders_brainstorm_and_add_detail_controls(self):
        project = self._create_project()
        self.client.force_login(self.user)

        response = self.client.get(reverse("comic_book:location-create", kwargs={"slug": project.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="comic-location-brainstorm-btn"', html=False)
        self.assertContains(response, 'id="comic-location-add-details-btn"', html=False)
        self.assertContains(response, 'id="comic-location-image-btn"', html=False)
        self.assertContains(response, 'enctype="multipart/form-data"', html=False)
        self.assertContains(response, 'name="image_upload"', html=False)
        self.assertContains(response, 'accept="image/*"', html=False)
        self.assertContains(response, reverse("comic_book:location-brainstorm", kwargs={"slug": project.slug}))
        self.assertContains(response, reverse("comic_book:location-add-details", kwargs={"slug": project.slug}))
        self.assertContains(response, reverse("comic_book:location-image-preview", kwargs={"slug": project.slug}))

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_preview_location_image_returns_project_style_image(self):
        project = self._create_project()
        ComicBible.objects.create(project=project, visual_rules="Signal glow marks forbidden infrastructure.")
        self.client.force_login(self.user)

        prompts = []

        def fake_generate_image_data_url(*, prompt, model_name, size):
            prompts.append(prompt)
            return "data:image/png;base64,locationpreview"

        with patch("comic_book.views.generate_image_data_url", side_effect=fake_generate_image_data_url):
            response = self.client.post(
                reverse("comic_book:location-image-preview", kwargs={"slug": project.slug}),
                data={
                    "name": "Static Market",
                    "description": "A black-market concourse built inside a dead transmitter.",
                    "visual_notes": "- Hanging cable veils.",
                    "continuity_notes": "- Security drones cannot enter the inner ring.",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "image_url": "data:image/png;base64,locationpreview"})
        self.assertEqual(len(prompts), 1)
        self.assertIn("Create a polished comic-book establishing shot", prompts[0])
        self.assertIn("Name: Static Market", prompts[0])
        self.assertIn("Comic bible visual rules: Signal glow marks forbidden infrastructure.", prompts[0])

    def test_location_create_saves_generated_image_from_hidden_input(self):
        project = self._create_project()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("comic_book:location-create", kwargs={"slug": project.slug}),
            data={
                "name": "Static Market",
                "description": "A black-market concourse built inside a dead transmitter.",
                "visual_notes": "",
                "continuity_notes": "",
                "image_data_url": "data:image/png;base64,location",
            },
        )

        self.assertEqual(response.status_code, 302)
        location = ComicLocation.objects.get(project=project, name="Static Market")
        self.assertEqual(location.image_data_url, "data:image/png;base64,location")

    def test_location_create_saves_uploaded_image_as_data_url(self):
        project = self._create_project()
        self.client.force_login(self.user)
        uploaded = SimpleUploadedFile(
            "market.png",
            b"\x89PNG\r\n\x1a\nuploaded-image",
            content_type="image/png",
        )

        response = self.client.post(
            reverse("comic_book:location-create", kwargs={"slug": project.slug}),
            data={
                "name": "Static Market",
                "description": "A black-market concourse built inside a dead transmitter.",
                "visual_notes": "",
                "continuity_notes": "",
                "image_upload": uploaded,
            },
        )

        self.assertEqual(response.status_code, 302)
        location = ComicLocation.objects.get(project=project, name="Static Market")
        self.assertTrue(location.image_data_url.startswith("data:image/png;base64,"))
        self.assertEqual(location.image_data_url, "data:image/png;base64,iVBORw0KGgp1cGxvYWRlZC1pbWFnZQ==")

    def test_location_update_save_redirects_back_to_same_location_edit_page(self):
        project = self._create_project()
        location = ComicLocation.objects.create(project=project, name="Static Market")
        self.client.force_login(self.user)

        edit_url = reverse("comic_book:location-edit", kwargs={"slug": project.slug, "pk": location.pk})
        response = self.client.post(
            edit_url,
            data={
                "name": "Static Market",
                "description": "A black-market concourse built inside a dead transmitter.",
                "visual_notes": "- Hanging cable veils.",
                "continuity_notes": "- Security drones cannot enter the inner ring.",
                "image_data_url": "data:image/png;base64,location",
            },
        )

        self.assertRedirects(response, edit_url, fetch_redirect_response=False)
        location.refresh_from_db()
        self.assertEqual(location.description, "A black-market concourse built inside a dead transmitter.")
        self.assertEqual(location.image_data_url, "data:image/png;base64,location")

    def test_location_brainstorm_returns_only_empty_field_suggestions(self):
        project = self._create_project()
        ComicBible.objects.create(project=project, visual_rules="Signal glow marks forbidden infrastructure.")
        ComicCharacter.objects.create(project=project, name="Nika Vale", role="Courier")
        ComicLocation.objects.create(project=project, name="Relay Port", description="A decaying orbital dock.")
        self.client.force_login(self.user)

        with patch(
            "comic_book.views.call_llm",
            return_value=LLMResult(
                text='{"name":"Static Market","description":"A black-market concourse built inside a dead transmitter.","visual_notes":"- Hanging cable veils.\\n- Vendor lights pulse like warning beacons.","continuity_notes":"- Security drones cannot enter the inner ring."}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ) as mock_call:
            response = self.client.post(
                reverse("comic_book:location-brainstorm", kwargs={"slug": project.slug}),
                data={
                    "name": "",
                    "description": "Existing location description.",
                    "visual_notes": "",
                    "continuity_notes": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "suggestions": {
                    "name": "Static Market",
                    "visual_notes": "- Hanging cable veils.\n- Vendor lights pulse like warning beacons.",
                    "continuity_notes": "- Security drones cannot enter the inner ring.",
                },
            },
        )
        prompt = mock_call.call_args.kwargs["prompt"]
        self.assertIn("Project title: Star Signal", prompt)
        self.assertIn("Comic bible visual rules: Signal glow marks forbidden infrastructure.", prompt)
        self.assertIn("Key characters:", prompt)
        self.assertIn("Key locations:", prompt)

    def test_location_brainstorm_passes_uploaded_image_to_model(self):
        project = self._create_project()
        self.client.force_login(self.user)

        with patch(
            "comic_book.views.call_llm",
            return_value=LLMResult(
                text='{"name":"Static Market","visual_notes":"- Hanging cable veils."}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ) as mock_call:
            response = self.client.post(
                reverse("comic_book:location-brainstorm", kwargs={"slug": project.slug}),
                data={
                    "name": "",
                    "description": "",
                    "visual_notes": "",
                    "continuity_notes": "",
                    "image_data_url": "data:image/png;base64,locationimage",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"ok": True, "suggestions": {"name": "Static Market", "visual_notes": "- Hanging cable veils."}},
        )
        self.assertEqual(mock_call.call_args.kwargs["image_data_url"], "data:image/png;base64,locationimage")
        self.assertIn("Image provided: yes", mock_call.call_args.kwargs["prompt"])

    def test_location_add_details_trims_overlapping_rewrite_to_prevent_duplication(self):
        project = self._create_project()
        self.client.force_login(self.user)

        with patch(
            "comic_book.views.call_llm",
            return_value=LLMResult(
                text='{"description":"A black-market concourse built inside a dead transmitter. Its lowest deck floods whenever the relay overheats."}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ):
            response = self.client.post(
                reverse("comic_book:location-add-details", kwargs={"slug": project.slug}),
                data={
                    "name": "Static Market",
                    "description": "A black-market concourse built inside a dead transmitter.",
                    "visual_notes": "",
                    "continuity_notes": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"ok": True, "suggestions": {"description": "Its lowest deck floods whenever the relay overheats."}},
        )

    def test_character_brainstorm_splits_inline_bullets_into_new_lines(self):
        project = self._create_project()
        self.client.force_login(self.user)

        with patch(
            "comic_book.views.call_llm",
            return_value=LLMResult(
                text='{"visual_notes":"- Sharp undercut silhouette. - Tired eyes. - Always scanning exits."}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ):
            response = self.client.post(
                reverse("comic_book:character-brainstorm", kwargs={"slug": project.slug}),
                data={
                    "name": "Sera Flint",
                    "role": "Cipher thief",
                    "age": "",
                    "gender": "",
                    "description": "",
                    "costume_notes": "",
                    "visual_notes": "",
                    "voice_notes": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["suggestions"]["visual_notes"],
            "- Sharp undercut silhouette.\n- Tired eyes.\n- Always scanning exits.",
        )

    def test_character_brainstorm_drops_brackets_and_apostrophes_from_bullets(self):
        project = self._create_project()
        self.client.force_login(self.user)

        with patch(
            "comic_book.views.call_llm",
            return_value=LLMResult(
                text="{\"voice_notes\":\"- [Clipped] tactical sentences. - Dry deflection when someone's pressing.\"}",
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ):
            response = self.client.post(
                reverse("comic_book:character-brainstorm", kwargs={"slug": project.slug}),
                data={
                    "name": "Sera Flint",
                    "role": "Cipher thief",
                    "age": "",
                    "gender": "",
                    "description": "",
                    "costume_notes": "",
                    "visual_notes": "",
                    "voice_notes": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["suggestions"]["voice_notes"],
            "- Clipped tactical sentences.\n- Dry deflection when someones pressing.",
        )

    def test_character_create_saves_generated_face_images_from_hidden_inputs(self):
        project = self._create_project()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("comic_book:character-create", kwargs={"slug": project.slug}),
            data={
                "name": "Sera Flint",
                "role": "Cipher thief",
                "age": "29",
                "gender": "Woman",
                "description": "Lean, guarded, and always reading exits before people.",
                "costume_notes": "Dark utility jacket with signal-thread seams.",
                "visual_notes": "Shaved sidecut, narrow face, tired eyes.",
                "voice_notes": "Short tactical phrasing with dry sarcasm.",
                "frontal_face_image_data_url": "data:image/png;base64,front",
                "sideways_face_image_data_url": "data:image/png;base64,side",
            },
        )

        self.assertEqual(response.status_code, 302)
        character = ComicCharacter.objects.get(project=project, name="Sera Flint")
        self.assertEqual(
            response["Location"],
            reverse("comic_book:character-edit", kwargs={"slug": project.slug, "pk": character.pk}),
        )
        self.assertEqual(character.age, 29)
        self.assertEqual(character.gender, "Woman")
        self.assertEqual(character.frontal_face_image_data_url, "data:image/png;base64,front")
        self.assertEqual(character.sideways_face_image_data_url, "data:image/png;base64,side")

    def test_character_create_saves_generated_full_body_image_from_hidden_input(self):
        project = self._create_project()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("comic_book:character-create", kwargs={"slug": project.slug}),
            data={
                "name": "Sera Flint",
                "role": "Cipher thief",
                "age": "29",
                "gender": "Woman",
                "description": "Lean, guarded, and always reading exits before people.",
                "costume_notes": "Dark utility jacket with signal-thread seams.",
                "visual_notes": "Shaved sidecut, narrow face, tired eyes.",
                "voice_notes": "Short tactical phrasing with dry sarcasm.",
                "full_body_image_data_url": "data:image/png;base64,body",
            },
        )

        self.assertEqual(response.status_code, 302)
        character = ComicCharacter.objects.get(project=project, name="Sera Flint")
        self.assertEqual(
            response["Location"],
            reverse("comic_book:character-edit", kwargs={"slug": project.slug, "pk": character.pk}),
        )
        self.assertEqual(character.age, 29)
        self.assertEqual(character.gender, "Woman")
        self.assertEqual(character.full_body_image_data_url, "data:image/png;base64,body")

    def test_character_create_accepts_large_generated_reference_images(self):
        project = self._create_project()
        self.client.force_login(self.user)
        large_image = "data:image/png;base64," + ("a" * 900_000)

        response = self.client.post(
            reverse("comic_book:character-create", kwargs={"slug": project.slug}),
            data={
                "name": "Sera Flint",
                "role": "Cipher thief",
                "age": "29",
                "gender": "Woman",
                "description": "Lean, guarded, and always reading exits before people.",
                "costume_notes": "Dark utility jacket with signal-thread seams.",
                "visual_notes": "Shaved sidecut, narrow face, tired eyes.",
                "voice_notes": "Short tactical phrasing with dry sarcasm.",
                "frontal_face_image_data_url": large_image,
                "sideways_face_image_data_url": large_image,
                "full_body_image_data_url": large_image,
            },
        )

        self.assertEqual(response.status_code, 302)
        character = ComicCharacter.objects.get(project=project, name="Sera Flint")
        self.assertEqual(character.frontal_face_image_data_url, large_image)
        self.assertEqual(character.sideways_face_image_data_url, large_image)
        self.assertEqual(character.full_body_image_data_url, large_image)

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_generate_character_faces_saves_both_project_style_images(self):
        project = self._create_project()
        project.art_style_notes = "Clean European sci-fi line art with restrained cel shading."
        project.save(update_fields=["art_style_notes", "updated_at"])
        character = ComicCharacter.objects.create(
            project=project,
            name="Sera Flint",
            role="Cipher thief",
            age=29,
            gender="Woman",
            description="Lean, guarded, and always reading exits before people.",
            costume_notes="Dark utility jacket with signal-thread seams.",
            visual_notes="Shaved sidecut, narrow face, tired eyes.",
            voice_notes="Short tactical phrasing with dry sarcasm.",
        )
        self.client.force_login(self.user)

        prompts = []
        sizes = []

        def fake_generate_image_data_url(*, prompt, model_name, size):
            prompts.append(prompt)
            sizes.append(size)
            return f"data:image/png;base64,{len(prompts)}"

        with patch("comic_book.views.generate_image_data_url", side_effect=fake_generate_image_data_url):
            response = self.client.post(
                reverse("comic_book:character-faces", kwargs={"slug": project.slug, "pk": character.pk}),
                data={
                    "name": "Sera Flint",
                    "role": "Cipher thief",
                    "age": "29",
                    "gender": "Woman",
                    "description": "Lean, guarded, and always reading exits before people.",
                    "costume_notes": "Dark utility jacket with signal-thread seams.",
                    "visual_notes": "Shaved sidecut, narrow face, tired eyes.",
                    "voice_notes": "Short tactical phrasing with dry sarcasm.",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "frontal_face_image_url": "data:image/png;base64,1",
                "sideways_face_image_url": "data:image/png;base64,2",
            },
        )
        character.refresh_from_db()
        self.assertEqual(character.frontal_face_image_data_url, "data:image/png;base64,1")
        self.assertEqual(character.sideways_face_image_data_url, "data:image/png;base64,2")
        self.assertEqual(len(prompts), 2)
        self.assertEqual(sizes, ["1024x1024", "1024x1024"])
        self.assertIn("Project style context:", prompts[0])
        self.assertIn("Art style notes: Clean European sci-fi line art with restrained cel shading.", prompts[0])
        self.assertIn("Pose target: straight-on frontal face.", prompts[0])
        self.assertIn("Pose target: sideways profile face.", prompts[1])
        self.assertIn("complete head, hairline, chin, neck, and upper shoulders", prompts[0])
        self.assertIn("small safety gap above the hair", prompts[0])
        self.assertIn("no large blank padding, no cropped head", prompts[0])
        self.assertIn("complete head, hairline, nose, chin, neck, and upper shoulders", prompts[1])
        self.assertIn("small safety gap above the hair", prompts[1])
        self.assertIn("Age: 29", prompts[0])
        self.assertIn("Gender: Woman", prompts[0])
        self.assertIn("Visual notes: Shaved sidecut, narrow face, tired eyes.", prompts[0])

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_generate_character_full_body_saves_project_style_image(self):
        project = self._create_project()
        project.art_style_notes = "Clean European sci-fi line art with restrained cel shading."
        project.save(update_fields=["art_style_notes", "updated_at"])
        character = ComicCharacter.objects.create(
            project=project,
            name="Sera Flint",
            role="Cipher thief",
            age=29,
            gender="Woman",
            description="Lean, guarded, and always reading exits before people.",
            costume_notes="Dark utility jacket with signal-thread seams.",
            visual_notes="Shaved sidecut, narrow face, tired eyes.",
            voice_notes="Short tactical phrasing with dry sarcasm.",
        )
        self.client.force_login(self.user)

        prompts = []
        sizes = []

        def fake_generate_image_data_url(*, prompt, model_name, size):
            prompts.append(prompt)
            sizes.append(size)
            return "data:image/png;base64,fullbody"

        with patch("comic_book.views.generate_image_data_url", side_effect=fake_generate_image_data_url):
            response = self.client.post(
                reverse("comic_book:character-full-body", kwargs={"slug": project.slug, "pk": character.pk}),
                data={
                    "name": "Sera Flint",
                    "role": "Cipher thief",
                    "age": "29",
                    "gender": "Woman",
                    "description": "Lean, guarded, and always reading exits before people.",
                    "costume_notes": "Dark utility jacket with signal-thread seams.",
                    "visual_notes": "Shaved sidecut, narrow face, tired eyes.",
                    "voice_notes": "Short tactical phrasing with dry sarcasm.",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "full_body_image_url": "data:image/png;base64,fullbody"})
        character.refresh_from_db()
        self.assertEqual(character.full_body_image_data_url, "data:image/png;base64,fullbody")
        self.assertEqual(len(prompts), 1)
        self.assertEqual(sizes, ["1024x1536"])
        self.assertIn("Pose target: full-body frontal view.", prompts[0])
        self.assertIn("very top of the hair to the soles of the boots", prompts[0])
        self.assertIn("complete top of the head", prompts[0])
        self.assertIn("Age: 29", prompts[0])
        self.assertIn("Gender: Woman", prompts[0])
        self.assertIn("Art style notes: Clean European sci-fi line art with restrained cel shading.", prompts[0])

    def test_comic_forms_mark_textareas_for_autogrow(self):
        forms_to_check = [
            ComicProjectForm(),
            ComicBibleForm(),
            ComicCharacterForm(),
            ComicLocationForm(),
            ComicIssueForm(),
            ComicPageForm(),
            ComicPanelForm(),
            ComicPanelNodeForm(),
        ]

        for form in forms_to_check:
            for field in form.fields.values():
                if field.widget.__class__.__name__ != "Textarea":
                    continue
                self.assertEqual(field.widget.attrs.get("data-autogrow"), "true")

    def test_panel_create_links_project_characters_and_location(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=3)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Arrival")
        character = ComicCharacter.objects.create(project=project, name="Nika Vale", role="Courier")
        location = ComicLocation.objects.create(project=project, name="Relay Port")
        self.client.force_login(self.user)

        response = self.client.post(
            reverse(
                "comic_book:panel-create",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk},
            ),
            data={
                "panel_number": 1,
                "title": "Dock reveal",
                "shot_type": ComicPanel.ShotType.WIDE,
                "focus": "Nika enters the ruined dock",
                "location": str(location.pk),
                "characters": [str(character.pk)],
                "action": "Nika steps through smoke as the warning beacon flickers.",
                "dialogue": "Nika: Someone wanted this place silent.",
                "caption": "Relay Port. Midnight shift.",
                "sfx": "BZZZT",
                "notes": "Keep the beacon visible in the background.",
            },
        )

        self.assertEqual(response.status_code, 302)
        panel = ComicPanel.objects.get(page=page, panel_number=1)
        self.assertEqual(panel.location_id, location.id)
        self.assertEqual(list(panel.characters.values_list("name", flat=True)), ["Nika Vale"])

    def test_shift_page_swaps_issue_order(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=2)
        page_one = ComicPage.objects.create(issue=issue, page_number=1, title="Old first")
        page_two = ComicPage.objects.create(issue=issue, page_number=2, title="Old second")
        self.client.force_login(self.user)

        response = self.client.post(
            reverse(
                "comic_book:page-shift",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "pk": page_two.pk},
            ),
            data={"direction": "up"},
        )

        self.assertEqual(response.status_code, 302)
        page_one.refresh_from_db()
        page_two.refresh_from_db()
        self.assertEqual(page_two.page_number, 1)
        self.assertEqual(page_one.page_number, 2)

    def test_swap_issues_swaps_issue_numbers(self):
        project = self._create_project()
        issue_one = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=2)
        issue_two = ComicIssue.objects.create(project=project, number=2, title="Issue Two", planned_page_count=2)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("comic_book:issue-swap", kwargs={"slug": project.slug}),
            data={"issue_id": str(issue_one.pk), "target_issue_id": str(issue_two.pk)},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        issue_one.refresh_from_db()
        issue_two.refresh_from_db()
        self.assertEqual(issue_one.number, 2)
        self.assertEqual(issue_two.number, 1)

    def test_issue_workspace_page_tiles_link_to_page_edit(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=2)
        ComicPage.objects.create(issue=issue, page_number=1, title="Old first")
        page_two = ComicPage.objects.create(issue=issue, page_number=2, title="Old second")
        self.client.force_login(self.user)

        response = self.client.get(reverse("comic_book:issue-workspace", kwargs={"slug": project.slug, "pk": issue.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse(
                "comic_book:page-edit",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "pk": page_two.pk},
            ),
        )
        self.assertNotContains(response, f'?page={page_two.pk}')

    def test_page_update_save_redirects_back_to_page_edit(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        self.client.force_login(self.user)

        response = self.client.post(
            reverse(
                "comic_book:page-edit",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "pk": page.pk},
            ),
            data={
                "page_number": 1,
                "title": "Saved opening page",
                "summary": "The courier arrives.",
                "page_role": ComicPage.PageRole.STORY,
                "layout_type": ComicPage.LayoutType.STANDARD,
                "page_turn_hook": "",
                "notes": "",
                "panel_layout": '{"type":"panel","panel_key":"root"}',
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "comic_book:page-edit",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "pk": page.pk},
            ),
            fetch_redirect_response=False,
        )
        self.assertNotIn("Page saved.", [str(message) for message in get_messages(response.wsgi_request)])

    def test_page_update_autosave_returns_json_without_save_message(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        self.client.force_login(self.user)

        response = self.client.post(
            reverse(
                "comic_book:page-edit",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "pk": page.pk},
            ),
            data={
                "page_number": 1,
                "title": "Autosaved opening page",
                "summary": "The courier leaves quietly.",
                "page_role": ComicPage.PageRole.STORY,
                "layout_type": ComicPage.LayoutType.STANDARD,
                "page_turn_hook": "",
                "notes": "",
                "panel_layout": '{"type":"panel","panel_key":"root"}',
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_X_COMIC_PAGE_AUTOSAVE="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        page.refresh_from_db()
        self.assertEqual(page.title, "Autosaved opening page")
        self.assertNotIn("Page saved.", [str(message) for message in get_messages(response.wsgi_request)])

    def test_page_update_autosave_validation_error_returns_json_error(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        self.client.force_login(self.user)

        response = self.client.post(
            reverse(
                "comic_book:page-edit",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "pk": page.pk},
            ),
            data={
                "page_number": "",
                "title": "Invalid autosave",
                "summary": "",
                "page_role": ComicPage.PageRole.STORY,
                "layout_type": ComicPage.LayoutType.STANDARD,
                "page_turn_hook": "",
                "notes": "",
                "panel_layout": '{"type":"panel","panel_key":"root"}',
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_X_COMIC_PAGE_AUTOSAVE="true",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"ok": False, "error": "Page autosave failed."})

    def test_page_update_normalizes_duplicate_panel_keys_for_unique_child_briefs(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        self.client.force_login(self.user)

        response = self.client.post(
            reverse(
                "comic_book:page-edit",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "pk": page.pk},
            ),
            data={
                "page_number": 1,
                "title": "Saved opening page",
                "summary": "The courier arrives.",
                "page_role": ComicPage.PageRole.STORY,
                "layout_type": ComicPage.LayoutType.STANDARD,
                "page_turn_hook": "",
                "notes": "",
                "panel_layout": json.dumps(
                    {
                        "type": "split",
                        "panel_key": "root",
                        "direction": "vertical",
                        "ratio": 0.5,
                        "children": [
                            {"type": "panel", "panel_key": "panel-2"},
                            {"type": "panel", "panel_key": "panel-2"},
                        ],
                    }
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        page.refresh_from_db()
        children = page.panel_layout["children"]
        self.assertEqual(children[0]["panel_key"], "panel-2")
        self.assertNotEqual(children[0]["panel_key"], children[1]["panel_key"])
        self.assertTrue(children[1]["panel_key"].startswith("panel-"))

    def test_page_update_preserves_existing_panel_layout_when_layout_post_is_empty(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        existing_layout = {
            "type": "split",
            "panel_key": "root",
            "direction": "vertical",
            "ratio": 0.5,
            "children": [
                {"type": "panel", "panel_key": "panel-1"},
                {"type": "panel", "panel_key": "panel-2"},
            ],
        }
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page", panel_layout=existing_layout)
        ComicPanelNode.objects.create(page=page, panel_key="panel-2", image_data_url="data:image/png;base64,saved")
        self.client.force_login(self.user)

        response = self.client.post(
            reverse(
                "comic_book:page-edit",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "pk": page.pk},
            ),
            data={
                "page_number": 1,
                "title": "Saved opening page",
                "summary": "The courier arrives.",
                "page_role": ComicPage.PageRole.STORY,
                "layout_type": ComicPage.LayoutType.STANDARD,
                "page_turn_hook": "",
                "notes": "",
                "panel_layout": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        page.refresh_from_db()
        self.assertEqual(page.panel_layout, existing_layout)
        self.assertTrue(page.panel_nodes.filter(panel_key="panel-2", image_data_url="data:image/png;base64,saved").exists())

    def test_page_update_does_not_collapse_existing_split_layout_without_explicit_reset(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        existing_layout = {
            "type": "split",
            "panel_key": "root",
            "direction": "vertical",
            "ratio": 0.5,
            "children": [
                {"type": "panel", "panel_key": "panel-1"},
                {"type": "panel", "panel_key": "panel-2"},
            ],
        }
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page", panel_layout=existing_layout)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse(
                "comic_book:page-edit",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "pk": page.pk},
            ),
            data={
                "page_number": 1,
                "title": "Saved opening page",
                "summary": "The courier arrives.",
                "page_role": ComicPage.PageRole.STORY,
                "layout_type": ComicPage.LayoutType.STANDARD,
                "page_turn_hook": "",
                "notes": "",
                "panel_layout": '{"type":"panel","panel_key":"root"}',
            },
        )

        self.assertEqual(response.status_code, 302)
        page.refresh_from_db()
        self.assertEqual(page.panel_layout, existing_layout)

    def test_page_update_allows_explicit_panel_layout_reset(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(
            issue=issue,
            page_number=1,
            title="Opening page",
            panel_layout={
                "type": "split",
                "panel_key": "root",
                "direction": "vertical",
                "ratio": 0.5,
                "children": [
                    {"type": "panel", "panel_key": "panel-1"},
                    {"type": "panel", "panel_key": "panel-2"},
                ],
            },
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse(
                "comic_book:page-edit",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "pk": page.pk},
            ),
            data={
                "page_number": 1,
                "title": "Saved opening page",
                "summary": "The courier arrives.",
                "page_role": ComicPage.PageRole.STORY,
                "layout_type": ComicPage.LayoutType.STANDARD,
                "page_turn_hook": "",
                "notes": "",
                "panel_layout": '{"type":"panel","panel_key":"root"}',
                "panel_layout_reset": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        page.refresh_from_db()
        self.assertEqual(page.panel_layout, {"type": "panel", "panel_key": "root"})

    def test_page_edit_includes_root_panel_image_for_hoverless_reload(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page", panel_layout={"type": "panel", "panel_key": "root"})
        ComicPanelNode.objects.create(page=page, panel_key="root", image_data_url="data:image/png;base64,rootimage")
        self.client.force_login(self.user)

        response = self.client.get(
            reverse(
                "comic_book:page-edit",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "pk": page.pk},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '"root": "data:image/png;base64,rootimage"', html=False)

    def test_page_update_preserves_panel_speech_bubbles(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        self.client.force_login(self.user)

        response = self.client.post(
            reverse(
                "comic_book:page-edit",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "pk": page.pk},
            ),
            data={
                "page_number": 1,
                "title": "Saved opening page",
                "summary": "The courier arrives.",
                "page_role": ComicPage.PageRole.STORY,
                "layout_type": ComicPage.LayoutType.STANDARD,
                "page_turn_hook": "",
                "notes": "",
                "panel_layout": json.dumps(
                    {
                        "type": "panel",
                        "panel_key": "root",
                        "speech_bubbles": [
                            {
                                "id": "speech-1",
                                "text": "We go now.",
                                "x": 12,
                                "y": 18,
                                "width": 32,
                                "height": 16,
                                "border_radius": 24,
                                "font_size": 22,
                                "pointer_x": 58,
                                "pointer_y": 72,
                                "flipped": True,
                            }
                        ],
                    }
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        page.refresh_from_db()
        self.assertEqual(page.panel_layout["speech_bubbles"][0]["text"], "We go now.")
        self.assertEqual(page.panel_layout["speech_bubbles"][0]["border_radius"], 24)
        self.assertEqual(page.panel_layout["speech_bubbles"][0]["font_size"], 22)
        self.assertEqual(page.panel_layout["speech_bubbles"][0]["pointer_x"], 58)
        self.assertEqual(page.panel_layout["speech_bubbles"][0]["pointer_y"], 72)
        self.assertNotIn("pointer_anchor_x", page.panel_layout["speech_bubbles"][0])
        self.assertNotIn("pointer_anchor_y", page.panel_layout["speech_bubbles"][0])
        self.assertTrue(page.panel_layout["speech_bubbles"][0]["flipped"])

    @override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
    def test_page_edit_panel_menu_renders_generate_control(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        ComicPanelNode.objects.create(page=page, panel_key="root", image_data_url="data:image/png;base64,saved")
        self.client.force_login(self.user)

        response = self.client.get(
            reverse(
                "comic_book:page-edit",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "pk": page.pk},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-panel-generate-url-template=', html=False)
        self.assertContains(response, 'data-panel-quick-prompt-url-template=', html=False)
        self.assertContains(response, 'data-panel-quick-prompt-accept-url-template=', html=False)
        self.assertContains(response, 'data-panel-quick-prompt-reject-url-template=', html=False)
        self.assertContains(response, 'data-panel-action="generate"', html=False)
        self.assertContains(response, 'data-panel-action="show-quick-prompt"', html=False)
        self.assertContains(response, 'data-panel-action="accept-quick-prompt"', html=False)
        self.assertContains(response, 'data-panel-action="reject-quick-prompt"', html=False)
        self.assertContains(response, 'data-panel-action="add-speech-bubble"', html=False)
        self.assertContains(response, ">Generate<", html=False)
        self.assertContains(response, ">Quick Prompt<", html=False)
        self.assertContains(response, "comic-panel-image-data")
        self.assertContains(response, "data:image/png;base64,saved")

    def test_panel_node_edit_renders_brainstorm_and_add_detail_controls(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        node = ComicPanelNode.objects.create(page=page, panel_key="panel-2")
        character = ComicCharacter.objects.create(project=project, name="Nika Vale", role="Courier")
        self.client.force_login(self.user)

        response = self.client.get(
            reverse(
                "comic_book:panel-node-edit",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="comic-panel-brainstorm-btn"', html=False)
        self.assertContains(response, 'id="comic-panel-add-details-btn"', html=False)
        self.assertContains(response, 'data-panel-character-menu', html=False)
        self.assertContains(response, 'name="characters"', html=False)
        self.assertContains(response, f'value="{character.id}"', html=False)
        self.assertContains(response, 'data-character-name="Nika Vale"', html=False)
        self.assertContains(
            response,
            reverse(
                "comic_book:panel-node-brainstorm",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
            ),
        )
        self.assertContains(
            response,
            reverse(
                "comic_book:panel-node-add-details",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
            ),
        )

    def test_panel_node_save_redirects_back_to_same_panel_edit_page(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        node = ComicPanelNode.objects.create(page=page, panel_key="panel-2")
        character = ComicCharacter.objects.create(project=project, name="Nika Vale", role="Courier")
        self.client.force_login(self.user)

        edit_url = reverse(
            "comic_book:panel-node-edit",
            kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
        )
        response = self.client.post(
            edit_url,
            data={
                "focus": "Nika at the threshold",
                "shot_type": ComicPanelNode.ShotType.WIDE,
                "camera_angle": "Low angle",
                "location": "",
                "characters": [str(character.pk)],
                "action": "Nika pauses under a flickering warning strip.",
                "mood": "Uneasy",
                "lighting_notes": "Cold warning light.",
                "dialogue_space": "Top-left clear",
                "must_include": "Relay lock",
                "must_avoid": "Crowded background",
                "style_override": "",
                "notes": "Keep silhouette readable.",
            },
        )

        self.assertRedirects(response, edit_url, fetch_redirect_response=False)
        node.refresh_from_db()
        self.assertEqual(node.focus, "Nika at the threshold")
        self.assertEqual(list(node.characters.values_list("name", flat=True)), ["Nika Vale"])

    def test_panel_node_brainstorm_returns_only_empty_field_suggestions(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", summary="The courier enters the relay.", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page", summary="Nika crosses the abandoned dock.")
        node = ComicPanelNode.objects.create(page=page, panel_key="panel-2", focus="Nika at the threshold")
        ComicCharacter.objects.create(project=project, name="Nika Vale", role="Courier")
        self.client.force_login(self.user)

        with patch(
            "comic_book.views.call_llm",
            return_value=LLMResult(
                text='{"focus":"Do not replace","characters":["Nika Vale","Unknown Extra"],"camera_angle":"Low angle from behind the broken hatch.","action":"Nika pauses under a flickering warning strip.","mood":"Uneasy","notes":"Keep the relay door readable in silhouette."}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ) as mock_call:
            response = self.client.post(
                reverse(
                    "comic_book:panel-node-brainstorm",
                    kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
                ),
                data={
                    "focus": "Nika at the threshold",
                    "shot_type": ComicPanelNode.ShotType.WIDE,
                    "camera_angle": "",
                    "action": "",
                    "mood": "",
                    "lighting_notes": "",
                    "dialogue_space": "",
                    "must_include": "",
                    "must_avoid": "",
                    "style_override": "",
                    "notes": "",
                    "location_label": "Relay Port",
                    "characters_label": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "suggestions": {
                    "characters": ["Nika Vale"],
                    "camera_angle": "Low angle from behind the broken hatch.",
                    "action": "Nika pauses under a flickering warning strip.",
                    "mood": "Uneasy",
                    "notes": "Keep the relay door readable in silhouette.",
                },
            },
        )
        prompt = mock_call.call_args.kwargs["prompt"]
        self.assertIn("Project title: Star Signal", prompt)
        self.assertIn("Page summary: Nika crosses the abandoned dock.", prompt)
        self.assertIn("Selected location: Relay Port", prompt)
        self.assertIn("Selected characters: ", prompt)
        self.assertIn("Available characters: Nika Vale", prompt)

    def test_panel_node_add_details_trims_overlapping_rewrite_to_prevent_duplication(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        node = ComicPanelNode.objects.create(page=page, panel_key="panel-2")
        ComicCharacter.objects.create(project=project, name="Nika Vale", role="Courier")
        ComicCharacter.objects.create(project=project, name="Sera Flint", role="Cipher thief")
        self.client.force_login(self.user)

        with patch(
            "comic_book.views.call_llm",
            return_value=LLMResult(
                text='{"characters":["Nika Vale","Sera Flint","Unknown Extra"],"action":"Nika pauses under a flickering warning strip. Her hand hovers over the sealed relay lock.","mood":"Do not replace"}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ):
            response = self.client.post(
                reverse(
                    "comic_book:panel-node-add-details",
                    kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
                ),
                data={
                    "focus": "Nika at the threshold",
                    "shot_type": ComicPanelNode.ShotType.WIDE,
                    "camera_angle": "",
                    "action": "Nika pauses under a flickering warning strip.",
                    "mood": "Uneasy",
                    "lighting_notes": "",
                    "dialogue_space": "",
                    "must_include": "",
                    "must_avoid": "",
                    "style_override": "",
                    "notes": "",
                    "characters_label": "Nika Vale",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"ok": True, "suggestions": {"characters": ["Sera Flint"], "action": "Her hand hovers over the sealed relay lock."}},
        )

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_generate_panel_node_image_uses_panel_brief_links_and_bible(self):
        project = self._create_project()
        project.art_style_notes = "Clean European sci-fi line art."
        project.save(update_fields=["art_style_notes", "updated_at"])
        ComicBible.objects.create(project=project, visual_rules="Signal glow marks forbidden infrastructure.")
        issue = ComicIssue.objects.create(
            project=project,
            number=1,
            title="Issue One",
            summary="The courier enters the relay.",
            notes="The relay lock is the emotional priority for this issue.",
            planned_page_count=1,
        )
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page", summary="Nika crosses the abandoned dock.")
        location = ComicLocation.objects.create(
            project=project,
            name="Relay Port",
            description="A decaying orbital dock.",
            visual_notes="- Hanging cable veils.",
        )
        character = ComicCharacter.objects.create(
            project=project,
            name="Nika Vale",
            role="Courier",
            description="Guarded courier with a rigid code.",
            costume_notes="Signal-thread jacket.",
            visual_notes="Sharp undercut silhouette.",
        )
        node = ComicPanelNode.objects.create(
            page=page,
            panel_key="panel-2",
            focus="Nika at the threshold",
            shot_type=ComicPanelNode.ShotType.WIDE,
            camera_angle="Low angle",
            location=location,
            action="Nika pauses under a flickering warning strip.",
            mood="Uneasy",
            lighting_notes="Cold warning light.",
            must_include="Relay lock",
            must_avoid="Crowded background",
        )
        node.characters.add(character)
        self.client.force_login(self.user)

        prompts = []

        def fake_generate_image_data_url(*, prompt, model_name, size):
            prompts.append(prompt)
            return "data:image/png;base64,panelimage"

        with patch("comic_book.views.generate_image_data_url", side_effect=fake_generate_image_data_url):
            response = self.client.post(
                reverse(
                    "comic_book:panel-node-generate",
                    kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
                ),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "image_url": "data:image/png;base64,panelimage"})
        node.refresh_from_db()
        self.assertEqual(node.image_data_url, "data:image/png;base64,panelimage")
        self.assertEqual(node.image_status, ComicPanelNode.ImageStatus.READY)
        self.assertEqual(len(prompts), 1)
        self.assertIn("Comic bible visual rules: Signal glow marks forbidden infrastructure.", prompts[0])
        self.assertIn("Location name: Relay Port", prompts[0])
        self.assertIn("Location visual notes: - Hanging cable veils.", prompts[0])
        self.assertIn("- Character: Nika Vale", prompts[0])
        self.assertIn("Costume notes: Signal-thread jacket.", prompts[0])
        self.assertIn("Focus: Nika at the threshold", prompts[0])
        self.assertIn("Must include: Relay lock", prompts[0])
        self.assertIn("Primary issue notes: The relay lock is the emotional priority for this issue.", prompts[0])
        self.assertNotIn("Issue notes: The relay lock is the emotional priority for this issue.", prompts[0])

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_generate_panel_node_image_saves_posted_page_layout_before_generation(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page", panel_layout={"type": "panel", "panel_key": "root"})
        posted_layout = {
            "type": "split",
            "panel_key": "root",
            "direction": "vertical",
            "ratio": 0.5,
            "children": [
                {"type": "panel", "panel_key": "panel-1"},
                {"type": "panel", "panel_key": "panel-2"},
            ],
        }
        self.client.force_login(self.user)

        with patch("comic_book.views.generate_image_data_url", return_value="data:image/png;base64,panelimage"):
            response = self.client.post(
                reverse(
                    "comic_book:panel-node-generate",
                    kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": "panel-2"},
                ),
                data={"panel_layout": json.dumps(posted_layout)},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        page.refresh_from_db()
        node = ComicPanelNode.objects.get(page=page, panel_key="panel-2")
        self.assertEqual(page.panel_layout, posted_layout)
        self.assertEqual(node.image_data_url, "data:image/png;base64,panelimage")

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_generate_panel_node_image_compacts_long_prompt_under_provider_limit(self):
        project = self._create_project()
        project.art_style_notes = "Style. " * 500
        project.save(update_fields=["art_style_notes", "updated_at"])
        ComicBible.objects.create(project=project, visual_rules="Visual rules. " * 500, continuity_rules="Continuity. " * 300)
        issue = ComicIssue.objects.create(
            project=project,
            number=1,
            title="Issue One",
            summary="Issue summary. " * 300,
            planned_page_count=1,
        )
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page", summary="Page summary. " * 300)
        node = ComicPanelNode.objects.create(
            page=page,
            panel_key="panel-2",
            focus="Nika at the threshold",
            action="Nika pauses under the warning strip. " * 80,
            must_include="Relay lock",
        )
        self.client.force_login(self.user)

        prompts = []

        def fake_generate_image_data_url(*, prompt, model_name, size):
            prompts.append(prompt)
            return "data:image/png;base64,panelimage"

        with patch("comic_book.views.generate_image_data_url", side_effect=fake_generate_image_data_url):
            response = self.client.post(
                reverse(
                    "comic_book:panel-node-generate",
                    kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
                ),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(prompts[0]), 3800)
        self.assertIn("panel brief:", prompts[0])
        self.assertIn("Focus: Nika at the threshold", prompts[0])
        self.assertIn("Must include: Relay lock", prompts[0])

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_generate_panel_node_image_returns_moderation_message_without_fallback(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        node = ComicPanelNode.objects.create(page=page, panel_key="panel-2", focus="Blocked prompt")
        self.client.force_login(self.user)

        calls = []

        def fake_generate_image_data_url(*, prompt, model_name, size):
            calls.append(model_name)
            raise Exception(
                "Error code: 400 - {'error': {'message': 'Your request was rejected by the safety system.', "
                "'type': 'image_generation_user_error', 'code': 'moderation_blocked'}}"
            )

        with patch("comic_book.views.generate_image_data_url", side_effect=fake_generate_image_data_url):
            response = self.client.post(
                reverse(
                    "comic_book:panel-node-generate",
                    kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
                ),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertIn("blocked by the safety system", response.json()["error"])
        self.assertEqual(calls, ["gpt-image-2"])
        node.refresh_from_db()
        self.assertEqual(node.image_status, ComicPanelNode.ImageStatus.FAILED)

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_generate_panel_node_image_does_not_fallback_from_gpt_image_2_failure(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        node = ComicPanelNode.objects.create(page=page, panel_key="panel-2", focus="Nika waits")
        self.client.force_login(self.user)

        calls = []

        def fake_generate_image_data_url(*, prompt, model_name, size):
            calls.append(model_name)
            raise Exception("Primary image model unavailable.")

        with patch("comic_book.views.generate_image_data_url", side_effect=fake_generate_image_data_url):
            response = self.client.post(
                reverse(
                    "comic_book:panel-node-generate",
                    kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
                ),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(calls, ["gpt-image-2"])

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_generate_panel_node_image_returns_specific_provider_error(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        node = ComicPanelNode.objects.create(page=page, panel_key="panel-2", focus="Nika waits")
        self.client.force_login(self.user)

        with patch("comic_book.views.generate_image_data_url", side_effect=Exception("Model does not exist.")):
            response = self.client.post(
                reverse(
                    "comic_book:panel-node-generate",
                    kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
                ),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Image generation failed: Model does not exist.", response.json()["error"])

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_quick_prompt_panel_node_image_uses_only_reference_image_prompt(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        node = ComicPanelNode.objects.create(page=page, panel_key="panel-2", image_data_url="data:image/png;base64,old")
        self.client.force_login(self.user)

        prompts = []

        def fake_edit_image_data_url(*, prompt, image_data_url, model_name, size):
            prompts.append(prompt)
            self.assertEqual(image_data_url, "data:image/png;base64,reference")
            self.assertEqual(model_name, "gpt-image-2")
            self.assertEqual(size, "1024x1024")
            return "data:image/png;base64,edited"

        with patch("comic_book.views.edit_image_data_url", side_effect=fake_edit_image_data_url):
            response = self.client.post(
                reverse(
                    "comic_book:panel-node-quick-prompt",
                    kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
                ),
                data={"prompt": "Make the background sunset.", "reference_image_data_url": "data:image/png;base64,reference"},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["image_url"], "data:image/png;base64,edited")
        self.assertTrue(response.json()["pending_token"])
        node.refresh_from_db()
        self.assertEqual(node.image_data_url, "data:image/png;base64,old")
        self.assertIn("Use the image itself as the only visual and story reference.", prompts[0])
        self.assertIn("Make the smallest localized edit", prompts[0])
        self.assertIn("color grading, exact palette, saturation, contrast, brightness", prompts[0])
        self.assertIn("Do not apply a global filter, haze, blur, fade, grain, static noise", prompts[0])
        self.assertIn("Make the background sunset.", prompts[0])

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_quick_prompt_panel_node_image_uses_saved_image_when_reference_not_posted(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        node = ComicPanelNode.objects.create(page=page, panel_key="panel-2", image_data_url="data:image/png;base64,saved")
        self.client.force_login(self.user)

        def fake_edit_image_data_url(*, prompt, image_data_url, model_name, size):
            self.assertEqual(image_data_url, "data:image/png;base64,saved")
            return "data:image/png;base64,edited"

        with patch("comic_book.views.edit_image_data_url", side_effect=fake_edit_image_data_url):
            response = self.client.post(
                reverse(
                    "comic_book:panel-node-quick-prompt",
                    kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
                ),
                data={"prompt": "Make the jacket blue."},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["image_url"], "data:image/png;base64,edited")

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_quick_prompt_panel_node_image_uses_uploaded_reference_image(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        node = ComicPanelNode.objects.create(page=page, panel_key="panel-2", image_data_url="data:image/png;base64,saved")
        self.client.force_login(self.user)

        def fake_edit_image_data_url(*, prompt, image_data_url, model_name, size):
            self.assertEqual(image_data_url, "data:image/png;base64,dXBsb2FkZWQ=")
            return "data:image/png;base64,edited"

        with patch("comic_book.views.edit_image_data_url", side_effect=fake_edit_image_data_url):
            response = self.client.post(
                reverse(
                    "comic_book:panel-node-quick-prompt",
                    kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
                ),
                data={
                    "prompt": "Make the jacket blue.",
                    "reference_image_upload": SimpleUploadedFile("reference.png", b"uploaded", content_type="image/png"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["image_url"], "data:image/png;base64,edited")

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_accept_quick_prompt_panel_node_image_persists_pending_preview(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        node = ComicPanelNode.objects.create(page=page, panel_key="panel-2", image_data_url="data:image/png;base64,saved")
        self.client.force_login(self.user)

        def fake_edit_image_data_url(*, prompt, image_data_url, model_name, size):
            return "data:image/png;base64,edited"

        with patch("comic_book.views.edit_image_data_url", side_effect=fake_edit_image_data_url):
            quick_response = self.client.post(
                reverse(
                    "comic_book:panel-node-quick-prompt",
                    kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
                ),
                data={"prompt": "Make the jacket blue."},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        node.refresh_from_db()
        self.assertEqual(node.image_data_url, "data:image/png;base64,saved")

        accept_response = self.client.post(
            reverse(
                "comic_book:panel-node-quick-prompt-accept",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
            ),
            data={"pending_token": quick_response.json()["pending_token"]},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(accept_response.status_code, 200)
        node.refresh_from_db()
        self.assertEqual(node.image_data_url, "data:image/png;base64,edited")

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_reject_quick_prompt_panel_node_image_keeps_original_image(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        node = ComicPanelNode.objects.create(page=page, panel_key="panel-2", image_data_url="data:image/png;base64,saved")
        self.client.force_login(self.user)

        with patch("comic_book.views.edit_image_data_url", return_value="data:image/png;base64,edited"):
            quick_response = self.client.post(
                reverse(
                    "comic_book:panel-node-quick-prompt",
                    kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
                ),
                data={"prompt": "Make the jacket blue."},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        reject_response = self.client.post(
            reverse(
                "comic_book:panel-node-quick-prompt-reject",
                kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
            ),
            data={"pending_token": quick_response.json()["pending_token"]},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(reject_response.status_code, 200)
        node.refresh_from_db()
        self.assertEqual(node.image_data_url, "data:image/png;base64,saved")

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-2")
    def test_quick_prompt_panel_node_image_returns_provider_billing_limit_message(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(project=project, number=1, title="Issue One", planned_page_count=1)
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        node = ComicPanelNode.objects.create(page=page, panel_key="panel-2", image_data_url="data:image/png;base64,saved")
        self.client.force_login(self.user)

        with patch(
            "comic_book.views.edit_image_data_url",
            side_effect=Exception(
                "Error code: 400 - {'error': {'message': 'Billing hard limit has been reached.', "
                "'type': 'billing_limit_user_error', 'code': 'billing_hard_limit_reached'}}"
            ),
        ):
            response = self.client.post(
                reverse(
                    "comic_book:panel-node-quick-prompt",
                    kwargs={"slug": project.slug, "issue_pk": issue.pk, "page_pk": page.pk, "panel_key": node.panel_key},
                ),
                data={"prompt": "Make the jacket blue."},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("billing hard limit", response.json()["error"])

    def test_issue_export_renders_panel_content(self):
        project = self._create_project()
        issue = ComicIssue.objects.create(
            project=project,
            number=1,
            title="Issue One",
            summary="The courier races the empire to the truth.",
            planned_page_count=1,
        )
        page = ComicPage.objects.create(issue=issue, page_number=1, title="Opening page")
        ComicPanel.objects.create(
            page=page,
            panel_number=1,
            shot_type=ComicPanel.ShotType.CLOSE,
            action="Nika studies a blood-red transmission on her wrist display.",
            dialogue="Nika: This should have stayed dead.",
            caption="Orbit above Vanta.",
            sfx="KRRK",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("comic_book:issue-export", kwargs={"slug": project.slug, "pk": issue.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Issue 1: Issue One")
        self.assertContains(response, "Page 1")
        self.assertContains(response, "Panel 1")
        self.assertContains(response, "This should have stayed dead.")

