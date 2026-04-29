package com.example.payment;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;

@Entity
public class PaymentEntity {
    @Id
    private final String id;
    private final String status;

    public PaymentEntity(String id, String status) {
        this.id = id;
        this.status = status;
    }

    public String status() {
        return status;
    }
}

