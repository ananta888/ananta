package com.example.ananta.model;

import java.util.HashSet;
import java.util.Set;

public class Video {
    private Long id;
    private String fileName;
    private String uploader;
    private Set<String> allowedUsers = new HashSet<>();

    public Video() {}
    public Video(Long id, String fileName, String uploader) {
        this.id = id;
        this.fileName = fileName;
        this.uploader = uploader;
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public String getFileName() { return fileName; }
    public void setFileName(String fileName) { this.fileName = fileName; }

    public String getUploader() { return uploader; }
    public void setUploader(String uploader) { this.uploader = uploader; }

    public Set<String> getAllowedUsers() { return allowedUsers; }
    public void setAllowedUsers(Set<String> allowedUsers) { this.allowedUsers = allowedUsers; }
}
