package com.example.ananta.repository;

import com.example.ananta.model.Course;
import org.springframework.stereotype.Repository;

import java.util.Collection;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

@Repository
public class CourseRepository {
    private final Map<Long, Course> courses = new ConcurrentHashMap<>();

    public Course save(Course course) {
        courses.put(course.getId(), course);
        return course;
    }

    public Optional<Course> findById(Long id) {
        return Optional.ofNullable(courses.get(id));
    }

    public Collection<Course> findAll() {
        return courses.values();
    }
}
