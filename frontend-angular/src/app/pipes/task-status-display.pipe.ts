import { Pipe, PipeTransform } from '@angular/core';
import { taskStatusDisplayLabel } from '../utils/task-status';

@Pipe({
  name: 'taskStatusDisplay',
  standalone: true
})
export class TaskStatusDisplayPipe implements PipeTransform {
  transform(status: string | undefined | null): string {
    return taskStatusDisplayLabel(status);
  }
}
