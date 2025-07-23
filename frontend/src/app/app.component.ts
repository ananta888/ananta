import { Component, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';

interface Course {
  id: number;
  name: string;
}

interface Video {
  id: number;
  fileName: string;
  uploader: string;
}

@Component({
  selector: 'app-root',
  templateUrl: './app.component.html'
})
export class AppComponent implements OnInit {
  courses: Course[] = [];
  newCourse = '';
  selectedCourse?: Course;
  selectedFile?: File;
  username = 'creator1';
  constructor(private http: HttpClient) {}

  ngOnInit() {
    this.loadCourses();
  }

  loadCourses() {
    this.http.get<Course[]>('/api/courses').subscribe(cs => this.courses = cs);
  }

  createCourse() {
    this.http.post<Course>('/api/courses', {name: this.newCourse}).subscribe(c => {
      this.courses.push(c);
      this.newCourse = '';
    });
  }

  selectCourse(c: Course) {
    this.selectedCourse = c;
  }

  onFileSelected(event: any) {
    this.selectedFile = event.target.files[0];
  }

  uploadVideo() {
    if (!this.selectedCourse || !this.selectedFile) { return; }
    const formData = new FormData();
    formData.append('uploader', this.username);
    formData.append('file', this.selectedFile);
    this.http.post(`/api/courses/${this.selectedCourse.id}/videos`, formData)
      .subscribe(() => alert('uploaded'));
  }
}
