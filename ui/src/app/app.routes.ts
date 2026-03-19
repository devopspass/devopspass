import { Routes } from '@angular/router';

import { AppComponent } from './app.component';
import { authGuard } from './auth.guard';
import { AuthFinishComponent } from './auth-finish.component';
import { LoginComponent } from './login.component';

export const appRoutes: Routes = [
  {
    path: '',
    component: AppComponent,
    canActivate: [authGuard],
  },
  {
    path: 'login',
    component: LoginComponent,
  },
  {
    path: 'auth/finish',
    component: AuthFinishComponent,
  },
  {
    path: '**',
    redirectTo: '',
  },
];
