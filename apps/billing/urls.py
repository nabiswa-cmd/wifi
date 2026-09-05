from django.urls import path
from . import views

app_name = 'billing'

urlpatterns = [
    path('buy/<int:package_id>/', views.initiate_purchase, name='initiate_purchase'),
    path('payment/<int:payment_id>/waiting/', views.payment_waiting, name='payment_waiting'),
    path('payment/<int:payment_id>/status/', views.payment_status, name='payment_status'),
]
