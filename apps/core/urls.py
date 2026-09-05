from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('login/', views.admin_login, name='admin_login'),
    path('', views.dashboard, name='dashboard'),
    path('payments/', views.payment_management, name='payments'),
    path('subscriptions/', views.subscription_management, name='subscriptions'),
]
