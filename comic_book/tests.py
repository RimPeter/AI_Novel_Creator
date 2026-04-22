from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import ComicCharacter, ComicIssue, ComicLocation, ComicPage, ComicPanel, ComicProject


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
