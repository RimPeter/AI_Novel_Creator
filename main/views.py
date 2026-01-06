from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView, ListView, DetailView
from .models import NovelProject
from .forms import NovelProjectForm

def home(request):
    return render(request, "main/base.html")


class ProjectListView(ListView):
    model = NovelProject
    template_name = "main/project_list.html"
    context_object_name = "projects"
    ordering = ["title"]


class ProjectDetailView(DetailView):
    model = NovelProject
    template_name = "main/project_detail.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"


class ProjectCreateView(CreateView):
    model = NovelProject
    form_class = NovelProjectForm
    template_name = "main/project_form.html"

    def get_success_url(self):
        return reverse_lazy("project-detail", kwargs={"slug": self.object.slug})


class ProjectUpdateView(UpdateView):
    model = NovelProject
    form_class = NovelProjectForm
    template_name = "main/project_form.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_success_url(self):
        return reverse_lazy("project-detail", kwargs={"slug": self.object.slug})

