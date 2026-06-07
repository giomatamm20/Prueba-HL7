<?php

use CodeIgniter\Router\RouteCollection;

/** @var RouteCollection $routes */
$routes->get('/', 'DashboardController::index');
$routes->get('/api/status', 'ApiController::status');
$routes->get('/api/messages', 'ApiController::messages');
$routes->get('/api/patients', 'ApiController::patients');
$routes->get('/api/orders', 'ApiController::orders');
$routes->get('/api/results', 'ApiController::results');
$routes->get('/api/test-catalog', 'ApiController::testCatalog');
$routes->post('/api/test-catalog', 'ApiController::createTestCatalogItem');
$routes->get('/api/outbound-orders', 'ApiController::outboundOrders');
$routes->post('/api/outbound-orders', 'ApiController::createOutboundOrder');
