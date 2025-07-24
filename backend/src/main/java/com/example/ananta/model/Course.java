package com.example.ananta.model;

import java.util.ArrayList;
import java.util.List;

public class Course {
    private Long id;
    private String name;
    private List<Video> videos = new ArrayList<>();

    public Course() {}
    public Course(Long id, String name) {
        this.id = id;
        this.name = name;
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public List<Video> getVideos() { return videos; }
    public void setVideos(List<Video> videos) { this.videos = videos; }
}
