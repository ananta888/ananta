package com.example.payment.config;

import com.example.payment.PaymentRepository;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class AppConfig {
    @Bean
    public PaymentRepository paymentRepository() {
        return new PaymentRepository();
    }
}

