package com.ananta.mobile.runtime;

public interface SpeechProvider extends ModelProvider {
    String transcribe(String audioPath);
}
