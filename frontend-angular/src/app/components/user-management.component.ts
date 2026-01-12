import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { UserAuthService } from '../services/user-auth.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-user-management',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="card">
      <h3>Benutzerverwaltung (Admin)</h3>
      <p class="muted">Hier können Sie Benutzer anlegen, löschen und Passwörter zurücksetzen.</p>

      <div style="margin-bottom: 20px; padding: 15px; background: rgba(0,0,0,0.05); border-radius: 8px;">
        <h4>Neuen Benutzer anlegen</h4>
        <div class="grid cols-3">
          <label>Benutzername
            <input [(ngModel)]="newUser.username" placeholder="Name">
          </label>
          <label>Passwort
            <input type="password" [(ngModel)]="newUser.password" placeholder="Passwort">
          </label>
          <label>Rolle
            <select [(ngModel)]="newUser.role">
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
          </label>
        </div>
        <button (click)="createUser()" style="margin-top: 10px;">Benutzer Erstellen</button>
      </div>

      <table>
        <thead>
          <tr>
            <th>Benutzername</th>
            <th>Rolle</th>
            <th>Aktionen</th>
          </tr>
        </thead>
        <tbody>
          <tr *ngFor="let user of users">
            <td>{{user.username}}</td>
            <td>
              <select [(ngModel)]="user.role" (change)="updateRole(user)">
                <option value="user">User</option>
                <option value="admin">Admin</option>
              </select>
            </td>
            <td>
              <button (click)="resetPassword(user)" class="button-outline" style="margin-right: 5px; padding: 2px 8px; font-size: 12px;">Reset PW</button>
              <button *ngIf="user.username !== 'admin'" (click)="deleteUser(user)" class="button-outline danger" style="padding: 2px 8px; font-size: 12px;">Löschen</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  `
})
export class UserManagementComponent implements OnInit {
  users: any[] = [];
  newUser = { username: '', password: '', role: 'user' };

  constructor(
    private auth: UserAuthService,
    private ns: NotificationService
  ) {}

  ngOnInit() {
    this.loadUsers();
  }

  loadUsers() {
    this.auth.getUsers().subscribe({
      next: users => this.users = users,
      error: () => this.ns.error('Benutzer konnten nicht geladen werden')
    });
  }

  createUser() {
    if (!this.newUser.username || !this.newUser.password) {
      this.ns.error('Benutzername und Passwort erforderlich');
      return;
    }
    this.auth.createUser(this.newUser.username, this.newUser.password, this.newUser.role).subscribe({
      next: () => {
        this.ns.success('Benutzer angelegt');
        this.newUser = { username: '', password: '', role: 'user' };
        this.loadUsers();
      },
      error: (err) => this.ns.error(err.error?.error || 'Fehler beim Anlegen')
    });
  }

  deleteUser(user: any) {
    if (!confirm(`Benutzer ${user.username} wirklich löschen?`)) return;
    this.auth.deleteUser(user.username).subscribe({
      next: () => {
        this.ns.success('Benutzer gelöscht');
        this.loadUsers();
      },
      error: () => this.ns.error('Löschen fehlgeschlagen')
    });
  }

  resetPassword(user: any) {
    const newPw = prompt(`Neues Passwort für ${user.username}:`);
    if (!newPw) return;
    this.auth.resetUserPassword(user.username, newPw).subscribe({
      next: () => this.ns.success('Passwort zurückgesetzt'),
      error: () => this.ns.error('Reset fehlgeschlagen')
    });
  }

  updateRole(user: any) {
    this.auth.updateUserRole(user.username, user.role).subscribe({
      next: () => this.ns.success('Rolle aktualisiert'),
      error: () => this.ns.error('Update fehlgeschlagen')
    });
  }
}
