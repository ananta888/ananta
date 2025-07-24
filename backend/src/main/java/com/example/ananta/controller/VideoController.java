package com.example.ananta.controller;

import com.example.ananta.model.Course;
import com.example.ananta.model.Video;
import com.example.ananta.repository.CourseRepository;
import com.example.ananta.service.VideoService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.Optional;
import java.util.Set;

@RestController
@RequestMapping("/api/courses/{courseId}/videos")
@CrossOrigin(origins = "*")
public class VideoController {

    private final VideoService videoService;
    private final CourseRepository courseRepository;

    public VideoController(VideoService videoService, CourseRepository courseRepository) {
        this.videoService = videoService;
        this.courseRepository = courseRepository;
    }

    @PostMapping
    public ResponseEntity<?> upload(@PathVariable Long courseId,
                                    @RequestParam String uploader,
                                    @RequestParam MultipartFile file) throws IOException {
        Optional<Video> video = videoService.uploadVideo(courseId, uploader, file);
        return video.<ResponseEntity<?>>map(ResponseEntity::ok)
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    @GetMapping
    public ResponseEntity<?> list(@PathVariable Long courseId) {
        Optional<Course> c = courseRepository.findById(courseId);
        return c.<ResponseEntity<?>>map(course -> ResponseEntity.ok(course.getVideos()))
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    @PostMapping("/{videoId}/permissions")
    public ResponseEntity<?> addPermission(@PathVariable Long courseId,
                                           @PathVariable Long videoId,
                                           @RequestParam String user) {
        Optional<Course> c = videoService.addPermission(courseId, videoId, user);
        return c.<ResponseEntity<?>>map(course -> ResponseEntity.ok().build())
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    @GetMapping("/{videoId}/permissions")
    public ResponseEntity<?> listPermissions(@PathVariable Long courseId,
                                             @PathVariable Long videoId) {
        Optional<Set<String>> p = videoService.getPermissions(courseId, videoId);
        return p.<ResponseEntity<?>>map(ResponseEntity::ok)
                .orElseGet(() -> ResponseEntity.notFound().build());
    }
}
