from django.urls import path
from . import views

app_name = 'customers'

urlpatterns = [
    path('', views.landing, name='landing'),          # captive portal landing (Section 9)
    path('login/', views.customer_login, name='login'),
    path('dashboard/', views.customer_dashboard, name='dashboard'),
    path('payment/success/', views.payment_success, name='payment_success'),
    path('payment/failed/', views.payment_failed, name='payment_failed'),
]
