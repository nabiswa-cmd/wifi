from django.urls import path
from . import views

app_name = 'vouchers'

urlpatterns = [
    path('redeem/', views.redeem_voucher, name='redeem'),
    path('<int:voucher_id>/status/', views.voucher_status, name='status'),
]