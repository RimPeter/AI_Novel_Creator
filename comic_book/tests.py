from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from unittest.mock import patch

from .forms import ComicBibleForm, ComicCharacterForm, ComicIssueForm, ComicLocationForm, ComicPageForm, ComicPanelForm, ComicProjectForm
from main.llm import LLMResult
from .models import ComicBible, ComicCharacter, ComicIssue, ComicLocation, ComicPage, ComicPanel, ComicProject


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

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-1")
    def test_preview_character_faces_returns_two_images_for_unsaved_character(self):
        project = self._create_project()
        self.client.force_login(self.user)

        prompts = []

        def fake_generate_image_data_url(*, prompt, model_name, size):
            prompts.append(prompt)
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
        self.assertIn("Pose target: straight-on frontal face.", prompts[0])
        self.assertIn("Pose target: sideways profile face.", prompts[1])

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-1")
    def test_preview_character_full_body_returns_image_for_unsaved_character(self):
        project = self._create_project()
        self.client.force_login(self.user)

        prompts = []

        def fake_generate_image_data_url(*, prompt, model_name, size):
            prompts.append(prompt)
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
        self.assertIn("Pose target: full-body frontal view.", prompts[0])

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
        self.assertContains(response, reverse("comic_book:location-brainstorm", kwargs={"slug": project.slug}))
        self.assertContains(response, reverse("comic_book:location-add-details", kwargs={"slug": project.slug}))
        self.assertContains(response, reverse("comic_book:location-image-preview", kwargs={"slug": project.slug}))

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-1")
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
        self.assertEqual(character.age, 29)
        self.assertEqual(character.gender, "Woman")
        self.assertEqual(character.full_body_image_data_url, "data:image/png;base64,body")

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-1")
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

        def fake_generate_image_data_url(*, prompt, model_name, size):
            prompts.append(prompt)
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
        self.assertIn("Project style context:", prompts[0])
        self.assertIn("Art style notes: Clean European sci-fi line art with restrained cel shading.", prompts[0])
        self.assertIn("Pose target: straight-on frontal face.", prompts[0])
        self.assertIn("Pose target: sideways profile face.", prompts[1])
        self.assertIn("Age: 29", prompts[0])
        self.assertIn("Gender: Woman", prompts[0])
        self.assertIn("Visual notes: Shaved sidecut, narrow face, tired eyes.", prompts[0])

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_IMAGE_MODEL="gpt-image-1")
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

        def fake_generate_image_data_url(*, prompt, model_name, size):
            prompts.append(prompt)
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
        self.assertIn("Pose target: full-body frontal view.", prompts[0])
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
                "canvas_layout": '{"type":"panel","canvas_key":"root"}',
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
