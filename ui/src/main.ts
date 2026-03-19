import { bootstrapApplication } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';

import { appRoutes } from './app/app.routes';
import { RootComponent } from './app/root.component';

bootstrapApplication(RootComponent, {
	providers: [provideRouter(appRoutes)],
}).catch((error) => console.error(error));
