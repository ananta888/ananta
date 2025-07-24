package com.example.ananta.controller;

import com.example.ananta.model.Course;
import com.example.ananta.repository.CourseRepository;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Collection;
import java.util.concurrent.atomic.AtomicLong;

@RestController
@RequestMapping("/api/courses")
@CrossOrigin(origins = "*")
public class CourseController {

    private final CourseRepository courseRepository;
    private final AtomicLong courseId = new AtomicLong(1);

    public CourseController(CourseRepository courseRepository) {
        this.courseRepository = courseRepository;
    }

    @PostMapping
    public Course create(@RequestBody Course course) {
        course.setId(courseId.getAndIncrement());
        return courseRepository.save(course);
    }

    @GetMapping
    public Collection<Course> all() {
        return courseRepository.findAll();
    }
}
