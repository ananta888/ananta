package com.example.ananta.service;

import com.example.ananta.model.Course;
import com.example.ananta.model.Video;
import com.example.ananta.repository.CourseRepository;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;

@Service
public class VideoService {
    private final CourseRepository courseRepository;
    private final AtomicLong videoId = new AtomicLong(1);
    private final Path baseDir = Paths.get("data/videos");

    public VideoService(CourseRepository courseRepository) throws IOException {
        this.courseRepository = courseRepository;
        Files.createDirectories(baseDir);
    }

    public Optional<Video> uploadVideo(Long courseId, String uploader, MultipartFile file) throws IOException {
        Optional<Course> courseOpt = courseRepository.findById(courseId);
        if (courseOpt.isEmpty()) {
            return Optional.empty();
        }
        Course course = courseOpt.get();
        Long id = videoId.getAndIncrement();
        Path target = baseDir.resolve(id + "_" + file.getOriginalFilename());
        Files.copy(file.getInputStream(), target);
        Video video = new Video(id, target.getFileName().toString(), uploader);
        video.getAllowedUsers().add(uploader); // uploader has access
        course.getVideos().add(video);
        return Optional.of(video);
    }

    public Optional<Course> addPermission(Long courseId, Long videoId, String user) {
        return courseRepository.findById(courseId).map(course -> {
            course.getVideos().stream()
                .filter(v -> v.getId().equals(videoId))
                .findFirst()
                .ifPresent(v -> v.getAllowedUsers().add(user));
            return course;
        });
    }

    public Optional<Set<String>> getPermissions(Long courseId, Long videoId) {
        return courseRepository.findById(courseId)
                .flatMap(course -> course.getVideos().stream()
                        .filter(v -> v.getId().equals(videoId))
                        .findFirst()
                        .map(Video::getAllowedUsers));
    }
}
