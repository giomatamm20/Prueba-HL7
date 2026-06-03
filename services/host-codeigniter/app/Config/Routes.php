<?php

use CodeIgniter\Router\RouteCollection;

/** @var RouteCollection $routes */
$routes->get('/', 'DashboardController::index');
$routes->get('/api/status', 'ApiController::status');
$routes->get('/api/messages', 'ApiController::messages');
$routes->get('/api/patients', 'ApiController::patients');
$routes->get('/api/orders', 'ApiController::orders');
$routes->get('/api/results', 'ApiController::results');
