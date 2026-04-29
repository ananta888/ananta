package com.example.payment;

public class PaymentRepository {
    public PaymentEntity findById(String id) {
        return new PaymentEntity(id, "ok");
    }
}

