from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import TemplateView

spa = TemplateView.as_view(template_name="index.html")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("monitor.urls")),
    path("", spa, name="spa"),
    # Vue Router 使用 history 模式，直接访问前端子路由时仍返回同一个入口模板。
    # API、管理页和静态资源保持 Django 自己的 404，不被 SPA 兜底吞掉。
    re_path(r"^(?!api/|admin/|static/).*$", spa, name="spa-fallback"),
]
