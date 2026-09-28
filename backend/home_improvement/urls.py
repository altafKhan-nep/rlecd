"""
URL configuration for home_improvement project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path,include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path("",include('main.urls'))
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# Uploaded images. Served in DEBUG by the staticfiles helper, and in production
# by Django's own media view so /media/ resolves on hosts that are not behind a
# separate static domain. Both routes are equivalent; the explicit one is only
# added outside DEBUG because the helper is a no-op there.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
