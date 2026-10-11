from django.urls import path

from . import views

app_name = 'loyalty'

urlpatterns = [
    path('', views.home, name='home'),
    path('lookup/', views.lookup, name='lookup'),
    path('verify/', views.verify, name='verify'),
    path('live/', views.live, name='live'),
    path('redeem/', views.redeem_view, name='redeem'),
    path('forget/', views.forget, name='forget'),
]
